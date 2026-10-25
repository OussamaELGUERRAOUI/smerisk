"""simulate_aggregate: sum uncertain components across multiple periods using Monte Carlo (§3.1).

Draws are processed in blocks of ``CHUNK_SIZE`` simulations (see ``core.montecarlo``): memory use stays bounded
regardless of the number of components, and variance contributions are accumulated block by block.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from smerisk.core.correlation import Correlation, prepare_correlation
from smerisk.core.distributions import Distribution, from_normal
from smerisk.core.envelope import InputModel, Result, Warning, envelope, parse_input
from smerisk.core.errors import (
    MAX_COMPONENTS,
    MAX_PERIODS,
    MAX_SIMULATIONS,
    FieldError,
    InvalidInput,
    check_limit,
)
from smerisk.core.montecarlo import (
    DEFAULT_QUANTILES,
    VarianceShares,
    cent,
    cents,
    chunks,
    high_uncertainty,
    quantile_key,
)
from smerisk.core.random import GENERATOR, make_streams, resolve_seed
from smerisk.core.types import Level, Period, period_kind
from smerisk.core.warnings import WarningCode
from smerisk.formulas.risk import Orientation, compute_risk_measures

FloatArray = NDArray[np.float64]


class Component(InputModel):
    id: str = Field(min_length=1)
    sign: Literal[1, -1]
    by_period: dict[Period, Distribution]
    correlation_group: str | None = Field(None, min_length=1)
    persistent: bool = False
    """One draw for the whole horizon, reused in every period: an event that happens or not for all periods (a
    hire), or a level that holds. By default each period draws again. In a correlation group, either every
    component is persistent (the group draws once) or none is."""


class AggregateInput(InputModel):
    periods: list[Period] = Field(min_length=1)
    opening_balance: float | None = None
    components: list[Component] = Field(min_length=1)
    correlation: Correlation | None = None
    n_simulations: int = Field(10_000, ge=1_000)
    seed: int | None = Field(None, ge=0)
    quantiles: list[Level] = Field(default_factory=lambda: list(DEFAULT_QUANTILES), min_length=1)
    threshold: float | None = None
    confidence: Level = 0.95
    orientation: Orientation = "higher_is_better"
    """``higher_is_better`` (cash: the risk is a low balance) or ``lower_is_better`` (a cost: the risk is an
    overrun). Sets the tail used by ``horizon.cfar`` and ``horizon.expected_shortfall``."""


class PeriodResult(BaseModel):
    period: str
    flow_quantiles: dict[str, float]
    cumulative_quantiles: dict[str, float]
    expected_flow: float
    expected_cumulative: float
    p_below_threshold: float | None


class Horizon(BaseModel):
    cfar: float
    expected_shortfall: float
    p_ever_below_threshold: float | None


class Contribution(BaseModel):
    id: str
    share: float
    """Share of horizon-result variance (covariance with the total / total variance); shares sum to 1."""


class AggregateResult(BaseModel):
    by_period: list[PeriodResult]
    horizon: Horizon
    contributions: list[Contribution]


@dataclass(frozen=True)
class _PeriodPlan:
    components: NDArray[np.intp]
    signs: FloatArray
    dists: list[Distribution]
    groups: Annotated[NDArray[np.intp], "correlation-group index; -1 means an independent draw"]


def simulate_aggregate(
    params: AggregateInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[AggregateResult]:
    started = time.perf_counter()
    inp = parse_input(AggregateInput, params, fields)
    _check(inp)
    seed = resolve_seed(inp.seed)
    warnings: list[Warning] = []

    # Persistent groups first: Cholesky is lower triangular, so their correlated draws only use their own draws and
    # stay constant across periods. Without persistent groups the order is unchanged (first appearance).
    named = list(dict.fromkeys(c.correlation_group for c in inp.components if c.correlation_group))
    held_groups = {c.correlation_group for c in inp.components if c.persistent and c.correlation_group}
    groups = [g for g in named if g in held_groups] + [g for g in named if g not in held_groups]
    group_index = {g: i for i, g in enumerate(groups)}
    cholesky, correlation_used = _correlation(inp.correlation, group_index, warnings)
    flows, shares = _simulate(
        _plan(inp, group_index),
        np.array([c.persistent and not c.correlation_group for c in inp.components]),
        len(held_groups),
        len(groups),
        cholesky,
        inp.n_simulations,
        seed,
    )

    quantiles = sorted(set(inp.quantiles))
    keys = [quantile_key(q) for q in quantiles]
    opening = inp.opening_balance or 0.0
    cumulative = opening + np.cumsum(flows, axis=1)
    flow_q = np.quantile(flows, quantiles, axis=0)
    cum_q = np.quantile(cumulative, quantiles, axis=0)
    flow_mean, cum_mean = flows.mean(axis=0), cumulative.mean(axis=0)
    below = None if inp.threshold is None else cumulative < inp.threshold

    by_period = [
        PeriodResult(
            period=period,
            flow_quantiles=dict(zip(keys, cents(flow_q[:, t]), strict=True)),
            cumulative_quantiles=dict(zip(keys, cents(cum_q[:, t]), strict=True)),
            expected_flow=cent(flow_mean[t]),
            expected_cumulative=cent(cum_mean[t]),
            p_below_threshold=None if below is None else float(below[:, t].mean()),
        )
        for t, period in enumerate(inp.periods)
    ]
    end = cumulative[:, -1]
    risk = compute_risk_measures(end, inp.confidence, inp.orientation)
    horizon = Horizon(
        cfar=cent(risk.var),
        expected_shortfall=cent(risk.expected_shortfall),
        p_ever_below_threshold=None if below is None else float(below.any(axis=1).mean()),
    )
    contributions = [
        Contribution(id=c.id, share=round(float(s), 6)) for c, s in zip(inp.components, shares, strict=True)
    ]
    warnings.extend(high_uncertainty(end))

    return envelope(
        AggregateResult(by_period=by_period, horizon=horizon, contributions=contributions),
        assumptions={
            "n_simulations": inp.n_simulations,
            "seed": seed,
            "quantiles": quantiles,
            "confidence": inp.confidence,
            "orientation": inp.orientation,
            "opening_balance": opening,
            "threshold": inp.threshold,
            "correlation": correlation_used,
            "copula": "gaussian",
            "random_streams": "one per ungrouped component and one per correlation group",
            "random_generator": GENERATOR,
            "quantile_method": "linear",
        },
        warnings=warnings,
        method="monte-carlo-gaussian-copula",
        started=started,
    )


def _check(inp: AggregateInput) -> None:
    """Check limits (413), then field consistency (422), reporting all field errors together."""
    check_limit("n_simulations", inp.n_simulations, MAX_SIMULATIONS)
    check_limit("components", len(inp.components), MAX_COMPONENTS)
    check_limit("periods", len(inp.periods), MAX_PERIODS)

    errors: list[FieldError] = []
    if len({period_kind(p) for p in inp.periods}) > 1:
        errors.append(FieldError(("periods",), "months and weeks cannot be mixed"))
    for i in range(1, len(inp.periods)):
        if inp.periods[i] <= inp.periods[i - 1]:
            errors.append(FieldError(("periods", i), "periods must be distinct and ordered"))
    known, seen = set(inp.periods), set()
    for i, c in enumerate(inp.components):
        if c.id in seen:
            errors.append(FieldError(("components", i, "id"), f"duplicate id: {c.id}"))
        if c.correlation_group and any(
            o.correlation_group == c.correlation_group and o.persistent != c.persistent for o in inp.components
        ):
            errors.append(
                FieldError(("components", i, "persistent"), "a correlation group mixes persistent and other components")
            )
        seen.add(c.id)
        for p in c.by_period:
            if p not in known:
                errors.append(FieldError(("components", i, "by_period", p), "period is not present in periods"))
    if inp.correlation is not None:
        groups = {c.correlation_group for c in inp.components}
        for j, v in enumerate(inp.correlation.variables):
            if v not in groups:
                errors.append(FieldError(("correlation", "variables", j), f"no component in group {v}"))
    if errors:
        raise InvalidInput(errors)


def _correlation(
    corr: Correlation | None, group_index: dict[str, int], warnings: list[Warning]
) -> tuple[FloatArray | None, dict[str, Any] | None]:
    """Cholesky factor of all group correlations (groups outside ``corr.variables`` are independent)."""
    if corr is None:
        return None, None
    idx = np.array([group_index[v] for v in corr.variables])
    full = np.eye(len(group_index))
    full[np.ix_(idx, idx)] = corr.as_array()
    prepared = prepare_correlation(full)
    if prepared.adjustment > 0:
        warnings.append(
            Warning(
                code=WarningCode.CORRELATION_ADJUSTED,
                message=f"matrix adjusted to be positive definite; maximum adjustment {prepared.adjustment:.4f}",
            )
        )
    used = np.round(prepared.spearman[np.ix_(idx, idx)], 4)
    return prepared.cholesky, {"variables": corr.variables, "matrix": used.tolist()}


def _plan(inp: AggregateInput, group_index: dict[str, int]) -> list[_PeriodPlan]:
    """List active components for each period (a missing period has value 0)."""
    plans = []
    for period in inp.periods:
        active = [(i, c) for i, c in enumerate(inp.components) if period in c.by_period]
        plans.append(
            _PeriodPlan(
                components=np.array([i for i, _ in active], dtype=np.intp),
                signs=np.array([float(c.sign) for _, c in active]).reshape(-1, 1),
                dists=[c.by_period[period] for _, c in active],
                groups=np.array(
                    [group_index[c.correlation_group] if c.correlation_group else -1 for _, c in active],
                    dtype=np.intp,
                ),
            )
        )
    return plans


def _simulate(
    plans: list[_PeriodPlan],
    held_components: NDArray[np.bool_],
    n_held_groups: int,
    n_groups: int,
    cholesky: FloatArray | None,
    n: int,
    seed: int,
) -> tuple[FloatArray, FloatArray]:
    """Return net flows (n × periods) and each component's share of horizon variance.

    Each ungrouped component draws from its own stream, only in periods where it is active. Each correlation group
    draws from its own stream in every period. A persistent ungrouped component (``held_components``) or persistent
    group (the first ``n_held_groups`` groups) draws once per block and reuses that draw in every period. Adding or
    removing a component therefore leaves the draws of the others unchanged; a group appended at the end leaves
    existing groups unchanged too (Cholesky is triangular).
    """
    n_components = len(held_components)
    component_rngs, group_rngs = make_streams(seed, n_components, n_groups)
    flows = np.zeros((n, len(plans)))
    shares = VarianceShares(n_components)
    for start, m in chunks(n):
        totals = np.zeros((n_components, m))
        held = {int(c): component_rngs[c].standard_normal(m) for c in np.flatnonzero(held_components)}
        held_groups = [group_rngs[g].standard_normal(m) for g in range(n_held_groups)]
        for t, plan in enumerate(plans):
            zg = (
                np.stack(held_groups + [rng.standard_normal(m) for rng in group_rngs[n_held_groups:]])
                if n_groups
                else None
            )
            if zg is not None and cholesky is not None:
                zg = cholesky @ zg
            if len(plan.components) == 0:
                continue
            z = np.empty((len(plan.components), m))
            for row, (c, g) in enumerate(zip(plan.components, plan.groups, strict=True)):
                if c in held:
                    z[row] = held[c]
                else:
                    z[row] = component_rngs[c].standard_normal(m) if g < 0 or zg is None else zg[g]
            y = plan.signs * from_normal(plan.dists, z)
            flows[start : start + m, t] = y.sum(axis=0)
            totals[plan.components] += y
        shares.add(totals)
    return flows, shares.shares()
