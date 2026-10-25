"""newsvendor and erlang_c (§7.2, §7.3): perishable order quantity and service-team sizing."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, model_validator
from scipy import special

from smerisk.core.distributions import Distribution, Empirical, from_normal
from smerisk.core.envelope import InputModel, Result, envelope, parse_input
from smerisk.core.errors import ComputationImpossible, check_limit
from smerisk.core.montecarlo import cent
from smerisk.core.types import Level

FloatArray = NDArray[np.float64]

# --- §7.2 Newsvendor ------------------------------------------------------------------------------------------

QUADRATURE_POINTS = 200_000
"""Midpoints in probability space used to integrate over the demand distribution (exact for ``empirical``)."""


class NewsvendorInput(InputModel):
    demand: Distribution
    """Demand of the period, in units (often ``empirical``: sales of the same past weekdays)."""
    unit_price: float = Field(ge=0)
    unit_cost: float = Field(ge=0)
    salvage_value: float = Field(0, ge=0)
    shortage_penalty: float = Field(0, ge=0)
    """Extra cost of a stock-out beyond the lost margin (an unhappy customer)."""
    current_quantity: float | None = Field(None, ge=0)


class QuantityOutcome(BaseModel):
    quantity: float
    expected_sales: float
    expected_leftover: float
    expected_shortage: float
    expected_profit: float


class NewsvendorResult(BaseModel):
    critical_ratio: float
    """c_u / (c_u + c_o): the service level aimed at."""
    optimal_quantity: int
    """F⁻¹(critical ratio), rounded up to a whole unit."""
    expected_sales: float
    expected_leftover: float
    expected_shortage: float
    expected_profit: float
    comparison: QuantityOutcome | None
    """The same measures for ``current_quantity``, if given."""


def newsvendor(params: NewsvendorInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[NewsvendorResult]:
    started = time.perf_counter()
    inp = parse_input(NewsvendorInput, params, fields)
    underage = inp.unit_price - inp.unit_cost + inp.shortage_penalty
    overage = inp.unit_cost - inp.salvage_value
    if overage <= 0:
        raise ComputationImpossible(
            "NO_FINITE_OPTIMUM",
            "salvage_value is at least unit_cost: an unsold unit costs nothing, so the optimal quantity is unbounded",
        )

    demand = _demand_points(inp.demand)
    if underage <= 0:
        ratio, quantity = 0.0, 0
    else:
        ratio = underage / (underage + overage)
        quantile = float(from_normal([inp.demand], np.array([[special.ndtri(ratio)]]))[0, 0])
        quantity = max(0, math.ceil(quantile - 1e-9))  # a quantile that is a whole number stays as it is
    best = _outcome(float(quantity), demand, inp)

    return envelope(
        NewsvendorResult(
            critical_ratio=ratio,
            optimal_quantity=quantity,
            expected_sales=best.expected_sales,
            expected_leftover=best.expected_leftover,
            expected_shortage=best.expected_shortage,
            expected_profit=best.expected_profit,
            comparison=None if inp.current_quantity is None else _outcome(inp.current_quantity, demand, inp),
        ),
        assumptions={
            "underage_cost": underage,
            "overage_cost": overage,
            "salvage_value": inp.salvage_value,
            "shortage_penalty": inp.shortage_penalty,
            "negative_demand": "counted as 0",
            "expectations": "exact over samples"
            if isinstance(inp.demand, Empirical)
            else f"quadrature on {QUADRATURE_POINTS} quantiles",
        },
        warnings=[],
        method="newsvendor",
        started=started,
    )


def _demand_points(demand: Distribution) -> FloatArray:
    """Equally likely demand values: the samples themselves, or quantiles at probability midpoints."""
    if isinstance(demand, Empirical):
        values = np.asarray(demand.samples, dtype=np.float64)
    else:
        u = (np.arange(QUADRATURE_POINTS) + 0.5) / QUADRATURE_POINTS
        values = from_normal([demand], special.ndtri(u)[None, :])[0]
    return np.maximum(values, 0.0)


def _outcome(quantity: float, demand: FloatArray, inp: NewsvendorInput) -> QuantityOutcome:
    sales = np.minimum(demand, quantity).mean()
    leftover = quantity - sales
    shortage = demand.mean() - sales
    profit = (
        inp.unit_price * sales
        + inp.salvage_value * leftover
        - inp.unit_cost * quantity
        - inp.shortage_penalty * shortage
    )
    return QuantityOutcome(
        quantity=quantity,
        expected_sales=float(sales),
        expected_leftover=float(leftover),
        expected_shortage=float(shortage),
        expected_profit=cent(profit),
    )


# --- §7.3 Erlang C --------------------------------------------------------------------------------------------

MAX_AGENTS = 10_000


class ErlangCInput(InputModel):
    arrival_rate: float = Field(gt=0)
    """λ: arrivals per hour."""
    service_time_minutes: float = Field(gt=0)
    agents: int | None = Field(None, ge=1)
    target_wait_seconds: float | None = Field(None, ge=0)
    target_service_level: Level | None = None
    """Share of customers served within ``target_wait_seconds``; with it, the smallest sufficient team is sought."""

    @model_validator(mode="after")
    def _check_targets(self) -> Self:
        if self.target_service_level is not None and self.target_wait_seconds is None:
            raise ValueError("target_service_level requires target_wait_seconds")
        return self


class Staffing(BaseModel):
    agents: int
    occupancy: float
    p_wait: float
    average_wait_seconds: float
    service_level: float | None
    """Share served within ``target_wait_seconds``; ``None`` without a wait target."""


class ErlangCResult(BaseModel):
    offered_load: float
    """A = λ / μ, in busy agents."""
    agents: int
    """Team size described by the fields below: ``agents`` if given, else the recommendation, else the smallest
    stable team."""
    occupancy: float
    p_wait: float
    average_wait_seconds: float
    service_level: float | None
    recommended_agents: int | None
    table: list[Staffing]
    """From the smallest stable team to two agents beyond the evaluated or recommended one."""


def erlang_c(params: ErlangCInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[ErlangCResult]:
    started = time.perf_counter()
    inp = parse_input(ErlangCInput, params, fields)
    mu = 60 / inp.service_time_minutes
    load = inp.arrival_rate / mu
    smallest = math.floor(load) + 1
    check_limit("agents needed for the offered load", smallest, MAX_AGENTS)
    if inp.agents is not None:
        check_limit("agents", inp.agents, MAX_AGENTS)
    if inp.agents is not None and inp.agents <= load:
        raise ComputationImpossible(
            "UNSTABLE_QUEUE",
            f"{inp.agents} agents for an offered load of {load:.2f}: the queue grows without limit",
            {"offered_load": load, "minimum_agents": smallest},
        )

    erlang = _ErlangB(load)

    def staffing(c: int) -> Staffing:
        return _staffing(c, erlang, load, inp.arrival_rate, mu, inp.target_wait_seconds)

    recommended = None
    if inp.target_service_level is not None:
        c = smallest
        while (staffing(c).service_level or 0.0) < inp.target_service_level:
            c += 1
            check_limit("recommended agents", c, MAX_AGENTS)
        recommended = c
    evaluated = inp.agents or recommended or smallest
    top = max(evaluated, recommended or 0) + 2
    table = [staffing(c) for c in range(min(smallest, evaluated), top + 1)]
    chosen = staffing(evaluated)

    return envelope(
        ErlangCResult(
            offered_load=load,
            agents=evaluated,
            occupancy=chosen.occupancy,
            p_wait=chosen.p_wait,
            average_wait_seconds=chosen.average_wait_seconds,
            service_level=chosen.service_level,
            recommended_agents=recommended,
            table=table,
        ),
        assumptions={
            "model": "M/M/c",
            "service_rate_per_hour": mu,
            "target_wait_seconds": inp.target_wait_seconds,
            "target_service_level": inp.target_service_level,
            "erlang_c_computation": "Erlang B recursion",
        },
        warnings=[],
        method="erlang-c",
        started=started,
    )


class _ErlangB:
    """Erlang B values B(0..c), extended on demand by the stable recursion B(k) = A·B(k−1) / (k + A·B(k−1))."""

    def __init__(self, load: float) -> None:
        self.load, self.values = load, [1.0]

    def p_wait(self, c: int) -> float:
        """Erlang C: probability that an arrival waits, with ``c`` agents."""
        while len(self.values) <= c:
            prev = self.values[-1]
            self.values.append(self.load * prev / (len(self.values) + self.load * prev))
        b = self.values[c]
        return c * b / (c - self.load * (1 - b))


def _staffing(
    c: int, erlang: _ErlangB, load: float, arrival_rate: float, mu: float, target_wait_seconds: float | None
) -> Staffing:
    p_wait = erlang.p_wait(c)
    spare = c * mu - arrival_rate  # per hour
    level = None if target_wait_seconds is None else 1 - p_wait * math.exp(-spare * target_wait_seconds / 3600)
    return Staffing(
        agents=c, occupancy=load / c, p_wait=p_wait, average_wait_seconds=p_wait / spare * 3600, service_level=level
    )
