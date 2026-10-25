"""markov_payments and expected_loss (§6.2, §6.3): receivables ageing and expected credit loss."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, model_validator

from smerisk.core.distributions import Beta
from smerisk.core.envelope import InputModel, Result, envelope, parse_input
from smerisk.core.errors import (
    MAX_OBSERVATIONS,
    MAX_PERIODS,
    MAX_SIMULATIONS,
    ComputationImpossible,
    FieldError,
    InvalidInput,
    check_limit,
)
from smerisk.core.montecarlo import DEFAULT_QUANTILES, cent, high_uncertainty, quantile_cents
from smerisk.core.random import GENERATOR, make_streams, resolve_seed
from smerisk.core.types import Level, Rate
from smerisk.formulas.risk import compute_risk_measures

FloatArray = NDArray[np.float64]

# --- §6.2 Markov chain of payments ----------------------------------------------------------------------------

PAID, DEFAULT = "PAID", "DEFAULT"
DEFAULT_STATES = ["CURRENT", "LATE_30", "LATE_60", "LATE_90", PAID, DEFAULT]


class Transition(InputModel):
    from_: str = Field(alias="from")
    to: str
    count: float = Field(ge=0)
    """Invoices, or amounts when ``weight_by`` is ``amount``, moving from one month-end to the next."""


class MarkovInput(InputModel):
    states: list[str] = Field(default_factory=lambda: list(DEFAULT_STATES))
    transitions: list[Transition] = Field(min_length=1)
    weight_by: Literal["count", "amount"] = "count"
    current_exposure: dict[str, float]
    horizon_periods: int = Field(6, ge=1)
    smoothing: float = Field(0.5, ge=0)

    @model_validator(mode="after")
    def _check_states(self) -> Self:
        if len(set(self.states)) != len(self.states):
            raise ValueError("states must be unique")
        if PAID not in self.states or DEFAULT not in self.states:
            raise ValueError(f"states must include the absorbing states {PAID} and {DEFAULT}")
        if len(self.states) < 3:
            raise ValueError("states must include at least one non-absorbing state")
        return self


class MonthProjection(BaseModel):
    month: int
    """1 = one month after today."""
    balances: dict[str, float]
    """Expected amount in each state at month end; PAID and DEFAULT are cumulative."""
    collections: float
    """Expected amount paid during the month."""
    new_defaults: float


class Absorption(BaseModel):
    p_default_from: dict[str, float]
    p_paid_from: dict[str, float]
    expected_months_to_payment: dict[str, float | None]
    """Expected months until payment for receivables that end up paid; ``None`` if payment is impossible."""
    expected_months_to_resolution: dict[str, float]
    """Expected months until paid or defaulted."""


class MarkovResult(BaseModel):
    transition_matrix: dict[str, dict[str, float]]
    projection: list[MonthProjection]
    absorption: Absorption
    expected_default_amount: float


def markov_payments(params: MarkovInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[MarkovResult]:
    started = time.perf_counter()
    inp = parse_input(MarkovInput, params, fields)
    check_limit("horizon_periods", inp.horizon_periods, MAX_PERIODS)
    check_limit("transitions", len(inp.transitions), MAX_OBSERVATIONS)
    _check_markov(inp)

    states = inp.states
    transient = [s for s in states if s not in (PAID, DEFAULT)]
    order = transient + [PAID, DEFAULT]  # canonical order: transient first
    index = {s: i for i, s in enumerate(order)}
    k = len(transient)

    counts = np.zeros((k, len(order)))
    for t in inp.transitions:
        counts[index[t.from_], index[t.to]] += t.count
    smoothed = counts + inp.smoothing
    totals = smoothed.sum(axis=1)
    if (totals == 0).any():
        empty = [transient[i] for i in np.flatnonzero(totals == 0)]
        raise ComputationImpossible(
            "NO_TRANSITIONS_FROM_STATE",
            f"no observed transition from {empty} and no smoothing: their probabilities are undefined",
            {"states": empty},
        )
    p = np.zeros((len(order), len(order)))
    p[:k] = smoothed / totals[:, None]
    p[k:, k:] = np.eye(2)
    _check_absorption(p, transient)

    q, r = p[:k, :k], p[:k, k:]
    n = np.linalg.inv(np.eye(k) - q)
    b = n @ r  # columns: PAID, DEFAULT
    steps = n.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        to_payment = (n @ b[:, 0]) / b[:, 0]

    exposure = np.zeros(len(order))
    for state, amount in inp.current_exposure.items():
        exposure[index[state]] = amount
    projection = []
    current = exposure
    for month in range(1, inp.horizon_periods + 1):
        following = current @ p
        projection.append(
            MonthProjection(
                month=month,
                balances={s: cent(following[index[s]]) for s in states},
                collections=cent(following[index[PAID]] - current[index[PAID]]),
                new_defaults=cent(following[index[DEFAULT]] - current[index[DEFAULT]]),
            )
        )
        current = following

    return envelope(
        MarkovResult(
            transition_matrix={s: {t: float(p[index[s], index[t]]) for t in states} for s in states},
            projection=projection,
            absorption=Absorption(
                p_default_from={s: float(b[i, 1]) for i, s in enumerate(transient)},
                p_paid_from={s: float(b[i, 0]) for i, s in enumerate(transient)},
                expected_months_to_payment={
                    s: float(to_payment[i]) if b[i, 0] > 0 else None for i, s in enumerate(transient)
                },
                expected_months_to_resolution={s: float(steps[i]) for i, s in enumerate(transient)},
            ),
            expected_default_amount=cent(float(exposure[:k] @ b[:, 1])),
        ),
        assumptions={
            "states": states,
            "absorbing_states": [PAID, DEFAULT],
            "weight_by": inp.weight_by,
            "smoothing": inp.smoothing,
            "smoothing_targets": "every state, from every non-absorbing state",
            "horizon_periods": inp.horizon_periods,
            "period": "month",
        },
        warnings=[],
        method="absorbing-markov-chain",
        started=started,
    )


def _check_markov(inp: MarkovInput) -> None:
    known = set(inp.states)
    errors: list[FieldError] = []
    for i, t in enumerate(inp.transitions):
        for field, state in (("from", t.from_), ("to", t.to)):
            if state not in known:
                errors.append(FieldError(("transitions", i, field), f"unknown state: {state}"))
        if t.from_ in (PAID, DEFAULT):
            errors.append(FieldError(("transitions", i, "from"), f"{t.from_} is absorbing: no transition leaves it"))
    for state, amount in inp.current_exposure.items():
        if state not in known:
            errors.append(FieldError(("current_exposure", state), f"unknown state: {state}"))
        elif state in (PAID, DEFAULT):
            errors.append(FieldError(("current_exposure", state), "exposure is held in non-absorbing states only"))
        if amount < 0:
            errors.append(FieldError(("current_exposure", state), "exposure must be non-negative"))
    if errors:
        raise InvalidInput(errors)


def _check_absorption(p: FloatArray, transient: list[str]) -> None:
    """Every non-absorbing state must reach PAID or DEFAULT, otherwise (I - Q) is singular."""
    k = len(transient)
    reaches = p[:k, k:].sum(axis=1) > 0
    while True:
        grown = reaches | ((p[:k, :k] > 0) & reaches[None, :]).any(axis=1)
        if (grown == reaches).all():
            break
        reaches = grown
    if not reaches.all():
        stuck = [transient[i] for i in np.flatnonzero(~reaches)]
        raise ComputationImpossible(
            "NO_ABSORPTION",
            f"receivables in {stuck} can never be paid or defaulted with these transitions",
            {"states": stuck},
        )


# --- §6.3 Expected credit loss --------------------------------------------------------------------------------

UNASSIGNED = "UNASSIGNED"
TOP_EXPOSURES = 5


class Exposure(InputModel):
    id: str = Field(min_length=1)
    ead: float = Field(ge=0)
    """Exposure at default, euros."""
    pd: Rate | Beta
    """Probability of default over the horizon, or a Beta distribution of it (e.g. a beta_binomial posterior)."""
    lgd: Rate = 1.0
    segment: str | None = Field(None, min_length=1)


class ExpectedLossInput(InputModel):
    exposures: list[Exposure] = Field(min_length=1)
    n_simulations: int | None = Field(None, ge=1_000)
    seed: int | None = Field(None, ge=0)
    quantiles: list[Level] = Field(default_factory=lambda: list(DEFAULT_QUANTILES), min_length=1)
    confidence: Level = 0.95


class ExpectedLossResult(BaseModel):
    expected_loss: float
    by_segment: dict[str, float]
    """Exposures without a segment are grouped under ``UNASSIGNED``."""
    concentration: float
    """Share of expected loss carried by the five largest expected losses."""
    loss_quantiles: dict[str, float] | None
    expected_shortfall: float | None
    """Mean total loss in the worst (1 − confidence) of simulations."""


def expected_loss(
    params: ExpectedLossInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[ExpectedLossResult]:
    started = time.perf_counter()
    inp = parse_input(ExpectedLossInput, params, fields)
    check_limit("exposures", len(inp.exposures), MAX_OBSERVATIONS)
    if inp.n_simulations is not None:
        check_limit("n_simulations", inp.n_simulations, MAX_SIMULATIONS)
    _check_unique_ids(inp.exposures)

    ead = np.array([e.ead for e in inp.exposures])
    lgd = np.array([e.lgd for e in inp.exposures])
    pd = np.array([e.pd.alpha / (e.pd.alpha + e.pd.beta) if isinstance(e.pd, Beta) else e.pd for e in inp.exposures])
    losses = pd * lgd * ead
    total = float(losses.sum())
    by_segment: dict[str, float] = {}
    for e, loss in zip(inp.exposures, losses, strict=True):
        key = e.segment or UNASSIGNED
        by_segment[key] = by_segment.get(key, 0.0) + float(loss)
    top = np.sort(losses)[::-1][:TOP_EXPOSURES].sum()

    quantiles, shortfall, warnings, seed = None, None, [], None
    if inp.n_simulations is not None:
        seed = resolve_seed(inp.seed)
        simulated = _simulate_losses(inp.exposures, ead * lgd, inp.n_simulations, seed)
        levels = sorted(set(inp.quantiles))
        quantiles = quantile_cents(simulated, levels)
        shortfall = cent(compute_risk_measures(simulated, inp.confidence, "lower_is_better").expected_shortfall)
        warnings = high_uncertainty(simulated)

    return envelope(
        ExpectedLossResult(
            expected_loss=cent(total),
            by_segment={k: cent(v) for k, v in sorted(by_segment.items())},
            concentration=float(top / total) if total > 0 else 0.0,
            loss_quantiles=quantiles,
            expected_shortfall=shortfall,
        ),
        assumptions={
            "pd_for_beta": "mean alpha / (alpha + beta) for the expectation; drawn per simulation",
            "defaults": "independent",
            "lgd_default": 1.0,
            "n_simulations": inp.n_simulations,
            "seed": seed,
            "quantiles": sorted(set(inp.quantiles)) if inp.n_simulations else None,
            "confidence": inp.confidence if inp.n_simulations else None,
            "random_streams": "one per exposure" if inp.n_simulations else None,
            "random_generator": GENERATOR if inp.n_simulations else None,
        },
        warnings=warnings,
        method="expected-loss" if inp.n_simulations is None else "expected-loss-monte-carlo",
        started=started,
    )


def _simulate_losses(exposures: list[Exposure], loss_given_default: FloatArray, n: int, seed: int) -> FloatArray:
    """Total portfolio loss per simulation; each exposure has its own stream and defaults independently."""
    (rngs,) = make_streams(seed, len(exposures))
    total = np.zeros(n)
    for e, amount, rng in zip(exposures, loss_given_default, rngs, strict=True):
        if amount == 0:
            continue
        p = rng.beta(e.pd.alpha, e.pd.beta, n) if isinstance(e.pd, Beta) else e.pd
        total += np.where(rng.random(n) < p, amount, 0.0)
    return total


def _check_unique_ids(exposures: list[Exposure]) -> None:
    seen: set[str] = set()
    errors = []
    for i, e in enumerate(exposures):
        if e.id in seen:
            errors.append(FieldError(("exposures", i, "id"), f"duplicate id: {e.id}"))
        seen.add(e.id)
    if errors:
        raise InvalidInput(errors)
