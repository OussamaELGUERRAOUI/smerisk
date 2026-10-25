"""utilization_forecast: Monte Carlo billable utilization and bench days of a consulting team (§4.3).

For each simulation, every current mission is renewed or not (Bernoulli, with a Beta-distributed probability when
``renewal_probability_beta`` is given) and every deal is won or not, with a drawn start month and duration. Month by
month, the open seats of won deals that are running are filled with free consultants:

- deals in order of start month, then input order;
- consultants in order of availability: free for the longest time first, then input order.

A seat that cannot be filled stays open and may be filled in a later month; until then its billable days count as
unstaffed. Simplifying assumption from the specification: any free consultant can staff any deal (no skills).

    utilization[t] = sum(billable_days[k, t]) / sum(available_days[k, t])
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, PositiveFloat, model_validator

from smerisk.core.distributions import Distribution, from_normal
from smerisk.core.envelope import InputModel, Result, envelope, parse_input
from smerisk.core.errors import (
    MAX_COMPONENTS,
    MAX_PERIODS,
    MAX_SIMULATIONS,
    FieldError,
    InvalidInput,
    check_limit,
)
from smerisk.core.montecarlo import DEFAULT_QUANTILES, chunks, high_uncertainty, quantile_cents, quantile_key
from smerisk.core.random import GENERATOR, make_streams, resolve_seed
from smerisk.core.types import Level, Month, Rate, month_index

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int32]

_NEVER = -(10**6)
"""Month index meaning "no assignment"; far below any horizon."""


class BetaParameters(InputModel):
    alpha: float = Field(gt=0)
    beta: float = Field(gt=0)


class CurrentMission(InputModel):
    end_period: Month
    billable_days_per_period: float = Field(ge=0)
    renewal_probability: Rate | None = None
    renewal_probability_beta: BetaParameters | None = None
    renewal_duration_months: Distribution | None = None
    """Months added when the mission is renewed; drawn values are rounded, minimum 1."""

    @model_validator(mode="after")
    def _check_renewal(self) -> Self:
        if (self.renewal_probability is None) == (self.renewal_probability_beta is None):
            raise ValueError("give exactly one of renewal_probability and renewal_probability_beta")
        if self.renewal_duration_months is None and self.renewal_probability != 0:
            raise ValueError("renewal_duration_months is required when the mission can be renewed")
        return self


class Consultant(InputModel):
    id: str = Field(min_length=1)
    available_days: dict[Month, float]
    """Available workdays per month; a month absent from the object counts as 0."""
    current_mission: CurrentMission | None = None

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if any(v < 0 for v in self.available_days.values()):
            raise ValueError("available_days must be non-negative")
        return self


class Deal(InputModel):
    id: str = Field(min_length=1)
    win_probability: Rate
    start_period: Month | Distribution
    """A month, or a distribution over the month index (0 = first period; drawn values are rounded)."""
    consultants_needed: int = Field(ge=1)
    billable_days_per_period: float = Field(ge=0)
    duration_months: PositiveFloat | Distribution
    """Rounded to whole months, minimum 1."""


class UtilizationInput(InputModel):
    periods: list[Month] = Field(min_length=1)
    consultants: list[Consultant] = Field(min_length=1)
    deals: list[Deal] = Field(default_factory=list)
    utilization_threshold: Rate | None = None
    n_simulations: int = Field(10_000, ge=1_000)
    seed: int | None = Field(None, ge=0)
    quantiles: list[Level] = Field(default_factory=lambda: list(DEFAULT_QUANTILES), min_length=1)


class PeriodUtilization(BaseModel):
    period: str
    utilization_quantiles: dict[str, float]
    bench_days_quantiles: dict[str, float]
    p_utilization_below: float | None


class ConsultantBench(BaseModel):
    id: str
    p_on_bench: dict[str, float]
    """Probability, per month, of having available days and no mission. A planning aid, never a judgment of a person."""


class UnstaffedDays(BaseModel):
    expected: float
    quantiles: dict[str, float]


class UtilizationResult(BaseModel):
    by_period: list[PeriodUtilization]
    consultants: list[ConsultantBench]
    unstaffed_deal_days: UnstaffedDays
    """Billable days of won deals over the horizon that the team could not staff (hiring or subcontracting need)."""


def utilization_forecast(
    params: UtilizationInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[UtilizationResult]:
    started = time.perf_counter()
    inp = parse_input(UtilizationInput, params, fields)
    available = _check(inp)
    seed = resolve_seed(inp.seed)

    utilization, bench_days, on_bench, unstaffed = _simulate(inp, available, seed)
    quantiles = sorted(set(inp.quantiles))
    n = inp.n_simulations
    threshold = inp.utilization_threshold
    by_period = [
        PeriodUtilization(
            period=period,
            utilization_quantiles={
                quantile_key(q): round(float(v), 6)
                for q, v in zip(quantiles, np.quantile(utilization[:, t], quantiles), strict=True)
            },
            bench_days_quantiles=quantile_cents(bench_days[:, t], quantiles),
            p_utilization_below=None if threshold is None else float((utilization[:, t] < threshold).mean()),
        )
        for t, period in enumerate(inp.periods)
    ]
    consultants = [
        ConsultantBench(id=c.id, p_on_bench={p: float(on_bench[k, t] / n) for t, p in enumerate(inp.periods)})
        for k, c in enumerate(inp.consultants)
    ]
    horizon_utilization = (utilization * available.sum(axis=0)).sum(axis=1) / available.sum()

    return envelope(
        UtilizationResult(
            by_period=by_period,
            consultants=consultants,
            unstaffed_deal_days=UnstaffedDays(
                expected=round(float(unstaffed.mean()), 2), quantiles=quantile_cents(unstaffed, quantiles)
            ),
        ),
        assumptions={
            "n_simulations": n,
            "seed": seed,
            "quantiles": quantiles,
            "utilization_threshold": threshold,
            "staffing_rule": "deals by start month then input order; consultants free longest first",
            "skills": "any free consultant can staff any deal",
            "unfilled_seats": "stay open and can be filled in a later month",
            "month_rounding": "drawn durations and start indices rounded to the nearest month, durations at least 1",
            "random_streams": "one per consultant and one per deal",
            "random_generator": GENERATOR,
            "quantile_method": "linear",
        },
        warnings=high_uncertainty(horizon_utilization),
        method="monte-carlo-staffing",
        started=started,
    )


def _check(inp: UtilizationInput) -> FloatArray:
    """Limits and cross-field checks; return available days (consultants × periods)."""
    check_limit("n_simulations", inp.n_simulations, MAX_SIMULATIONS)
    check_limit("periods", len(inp.periods), MAX_PERIODS)
    check_limit("consultants + deals", len(inp.consultants) + len(inp.deals), MAX_COMPONENTS)

    errors: list[FieldError] = []
    first = month_index(inp.periods[0])
    for t, p in enumerate(inp.periods):
        if month_index(p) != first + t:
            errors.append(FieldError(("periods", t), "periods must be consecutive months in order"))
    known = set(inp.periods)
    for field, ids in (("consultants", [c.id for c in inp.consultants]), ("deals", [d.id for d in inp.deals])):
        seen: set[str] = set()
        for i, id_ in enumerate(ids):
            if id_ in seen:
                errors.append(FieldError((field, i, "id"), f"duplicate id: {id_}"))
            seen.add(id_)
    for k, c in enumerate(inp.consultants):
        for p in c.available_days:
            if p not in known:
                errors.append(FieldError(("consultants", k, "available_days", p), "month not in periods"))
    if errors:
        raise InvalidInput(errors)

    available = np.array([[c.available_days.get(p, 0.0) for p in inp.periods] for c in inp.consultants])
    for empty in np.flatnonzero(available.sum(axis=0) == 0):
        errors.append(FieldError(("periods", int(empty)), "no consultant has available days this month"))
    if errors:
        raise InvalidInput(errors)
    return available


def _months(value: float | Distribution, rng: np.random.Generator, m: int, minimum: int | None) -> IntArray:
    """Whole months: a fixed number or rounded draws of a distribution."""
    if isinstance(value, int | float):
        drawn = np.full(m, float(value))
    else:
        drawn = from_normal([value], rng.standard_normal((1, m)))[0]
    months = np.rint(drawn).astype(np.int32)
    return months if minimum is None else np.maximum(months, minimum)


def _simulate(
    inp: UtilizationInput, available: FloatArray, seed: int
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """Return utilization and bench days (n × periods), bench counts (consultants × periods), unstaffed days (n)."""
    n, n_periods = inp.n_simulations, len(inp.periods)
    n_consultants, n_deals = len(inp.consultants), len(inp.deals)
    first = month_index(inp.periods[0])
    consultant_rngs, deal_rngs = make_streams(seed, n_consultants, n_deals)
    mission_days = np.array(
        [c.current_mission.billable_days_per_period if c.current_mission else 0.0 for c in inp.consultants]
    )
    deal_days = np.array([d.billable_days_per_period for d in inp.deals])
    needed = np.array([d.consultants_needed for d in inp.deals], dtype=np.int32)
    total_available = available.sum(axis=0)

    utilization, bench_days = np.empty((n, n_periods)), np.empty((n, n_periods))
    on_bench = np.zeros((n_consultants, n_periods))
    unstaffed = np.zeros(n)

    for start, m in chunks(n):
        rows = slice(start, start + m)
        # Last month of each consultant's current mission, renewal included.
        mission_end = np.full((m, n_consultants), _NEVER, dtype=np.int32)
        for k, (c, rng) in enumerate(zip(inp.consultants, consultant_rngs, strict=True)):
            mission = c.current_mission
            if mission is None:
                continue
            if mission.renewal_probability_beta is not None:
                p: FloatArray | float = rng.beta(
                    mission.renewal_probability_beta.alpha, mission.renewal_probability_beta.beta, m
                )
            else:
                p = mission.renewal_probability or 0.0
            renewed = rng.random(m) < p
            extra = (
                _months(mission.renewal_duration_months, rng, m, minimum=1)
                if mission.renewal_duration_months is not None
                else np.zeros(m, dtype=np.int32)
            )
            mission_end[:, k] = month_index(mission.end_period) - first + np.where(renewed, extra, 0)

        won = np.zeros((m, n_deals), dtype=bool)
        deal_start = np.zeros((m, n_deals), dtype=np.int32)
        deal_end = np.zeros((m, n_deals), dtype=np.int32)
        for j, (d, rng) in enumerate(zip(inp.deals, deal_rngs, strict=True)):
            won[:, j] = rng.random(m) < d.win_probability
            if isinstance(d.start_period, str):
                deal_start[:, j] = month_index(d.start_period) - first
            else:
                deal_start[:, j] = _months(d.start_period, rng, m, minimum=None)
            deal_end[:, j] = deal_start[:, j] + _months(d.duration_months, rng, m, minimum=1) - 1
        open_seats = np.where(won, needed, 0).astype(np.int32)
        # Deal priority within each simulation: start month, then input order.
        deal_order = np.argsort(deal_start * max(n_deals, 1) + np.arange(n_deals), axis=1, kind="stable").astype(
            np.int32
        )

        assign_end = np.full((m, n_consultants), _NEVER, dtype=np.int32)
        assign_days = np.zeros((m, n_consultants))
        for t in range(n_periods):
            busy = (mission_end >= t) | (assign_end >= t)
            running = won & (deal_start <= t) & (t <= deal_end)
            if n_deals:
                # Only simulations with an open seat and a free consultant need staffing this month.
                sims = np.flatnonzero((running & (open_seats > 0)).any(axis=1) & ~busy.all(axis=1)).astype(np.int32)
                if sims.size:
                    _staff(
                        sims,
                        busy,
                        mission_end,
                        assign_end,
                        assign_days,
                        running,
                        open_seats,
                        deal_order,
                        deal_end,
                        deal_days,
                    )
                    busy = (mission_end >= t) | (assign_end >= t)

            days_t = available[:, t]
            billable = np.where(
                mission_end >= t,
                np.minimum(mission_days, days_t),
                np.where(assign_end >= t, np.minimum(assign_days, days_t), 0.0),
            )
            bench = ~busy & (days_t > 0)
            utilization[rows, t] = billable.sum(axis=1) / total_available[t]
            bench_days[rows, t] = bench @ days_t
            on_bench[:, t] += bench.sum(axis=0)
            if n_deals:
                unstaffed[rows] += (np.where(running, open_seats, 0) * deal_days).sum(axis=1)

    return utilization, bench_days, on_bench, unstaffed


def _staff(
    sims: IntArray,
    busy: NDArray[np.bool_],
    mission_end: IntArray,
    assign_end: IntArray,
    assign_days: FloatArray,
    running: NDArray[np.bool_],
    open_seats: IntArray,
    deal_order: IntArray,
    deal_end: IntArray,
    deal_days: FloatArray,
) -> None:
    """Fill open seats of running deals with free consultants, in priority order, for the simulations ``sims``.

    Open seats, listed in deal priority order, form consecutive rank intervals; the free consultant of rank ``r``
    staffs the deal whose interval contains ``r``. One flat ``searchsorted`` handles every simulation at once.
    """
    n, n_consultants = len(sims), busy.shape[1]
    free = ~busy[sims]
    # Rank free consultants by availability (the earlier their last assignment ended, the earlier they are picked).
    last_end = np.maximum(mission_end[sims], assign_end[sims])
    key = np.where(free, last_end * n_consultants + np.arange(n_consultants), np.iinfo(np.int32).max)
    order = np.argsort(key, axis=1, kind="stable")
    rank = np.empty_like(order)
    rank[np.arange(n)[:, None], order] = np.arange(n_consultants)
    n_free = free.sum(axis=1)

    priority = deal_order[sims]
    seats = np.take_along_axis(np.where(running[sims], open_seats[sims], 0), priority, axis=1)
    filled_to = np.cumsum(seats, axis=1)
    taken = np.clip(n_free[:, None] - (filled_to - seats), 0, seats)
    remaining = np.empty_like(seats)
    np.put_along_axis(remaining, priority, seats - taken, axis=1)
    open_seats[sims] = np.where(running[sims], remaining, open_seats[sims])

    # Deal position (in priority order) for each consultant rank; ranks beyond the filled seats are not picked.
    stride = int(filled_to[:, -1].max()) + n_consultants + 1
    offsets = np.arange(n)[:, None] * stride
    position = np.searchsorted((filled_to + offsets).ravel(), (rank + offsets).ravel(), side="right")
    position = np.minimum(
        position.reshape(n, n_consultants) - np.arange(n)[:, None] * seats.shape[1], seats.shape[1] - 1
    )
    picked = rank < np.minimum(filled_to[:, -1], n_free)[:, None]
    deal = np.take_along_axis(priority, position, axis=1)
    rows = sims[:, None]
    assign_end[sims] = np.where(picked, deal_end[rows, deal], assign_end[sims])
    assign_days[sims] = np.where(picked, deal_days[deal], assign_days[sims])
