"""fixed_price_completion: Monte Carlo final cost, margin, and schedule of a fixed-price project (§4.2).

For each simulation, every remaining task draws its effort from a Beta-PERT law on [optimistic, pessimistic], every
risk occurs with its probability and then adds its drawn impact days, and one daily cost is drawn for the whole
remaining effort:

    EAC = actual_cost + (sum(task_days) + sum(occurs_r * impact_days_r)) * daily_cost
    margin = contract_price - EAC
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, model_validator

from smerisk.core.distributions import Distribution, Pert, from_normal
from smerisk.core.envelope import InputModel, Result, envelope, parse_input
from smerisk.core.errors import MAX_COMPONENTS, MAX_SIMULATIONS, FieldError, InvalidInput, check_limit
from smerisk.core.montecarlo import (
    DEFAULT_QUANTILES,
    VarianceShares,
    chunks,
    high_uncertainty,
    quantile_cents,
    quantile_key,
)
from smerisk.core.random import GENERATOR, make_streams, resolve_seed
from smerisk.core.types import Level, Rate
from smerisk.formulas.pert import Task

FloatArray = NDArray[np.float64]

DEFAULT_TEAM_CAPACITY = 1.0
"""Effort days delivered per workday when ``deadline_days`` is given without ``team_capacity_days_per_period``."""


class Risk(InputModel):
    id: str = Field(min_length=1)
    probability: Rate
    impact_days: Distribution


class CompletionInput(InputModel):
    contract_price: float = Field(gt=0)
    actual_cost: float = Field(ge=0)
    cost_budget: float | None = Field(None, gt=0)
    remaining_tasks: list[Task]
    daily_cost: Distribution
    risks: list[Risk] = Field(default_factory=list)
    deadline_days: float | None = Field(None, gt=0)
    team_capacity_days_per_period: float | None = Field(None, gt=0)
    n_simulations: int = Field(10_000, ge=1_000)
    seed: int | None = Field(None, ge=0)
    quantiles: list[Level] = Field(default_factory=lambda: list(DEFAULT_QUANTILES), min_length=1)

    @model_validator(mode="after")
    def _check_capacity(self) -> Self:
        if self.team_capacity_days_per_period is not None and self.deadline_days is None:
            raise ValueError("team_capacity_days_per_period requires deadline_days")
        return self


class Contribution(BaseModel):
    id: str
    kind: Literal["task", "risk"]
    share: float
    """Share of margin variance, Cov(item cost, remaining cost) / Var(remaining cost); shares sum to 1."""


class CompletionResult(BaseModel):
    eac_quantiles: dict[str, float]
    margin_quantiles: dict[str, float]
    margin_rate_quantiles: dict[str, float]
    p_loss: float
    p_within_budget: float | None
    p_on_time: float | None
    risk_contributions: list[Contribution]


def fixed_price_completion(
    params: CompletionInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[CompletionResult]:
    started = time.perf_counter()
    inp = parse_input(CompletionInput, params, fields)
    check_limit("n_simulations", inp.n_simulations, MAX_SIMULATIONS)
    check_limit("remaining_tasks + risks", len(inp.remaining_tasks) + len(inp.risks), MAX_COMPONENTS)
    _check_unique_ids(inp)
    seed = resolve_seed(inp.seed)

    eac, effort, shares = _simulate(inp, seed)
    margin = inp.contract_price - eac
    quantiles = sorted(set(inp.quantiles))
    capacity = None
    if inp.deadline_days is not None:
        capacity = inp.team_capacity_days_per_period or DEFAULT_TEAM_CAPACITY

    result = CompletionResult(
        eac_quantiles=quantile_cents(eac, quantiles),
        margin_quantiles=quantile_cents(margin, quantiles),
        margin_rate_quantiles={
            quantile_key(q): round(float(v), 6)
            for q, v in zip(quantiles, np.quantile(margin / inp.contract_price, quantiles), strict=True)
        },
        p_loss=float((margin < 0).mean()),
        p_within_budget=None if inp.cost_budget is None else float((eac <= inp.cost_budget).mean()),
        p_on_time=(
            None
            if inp.deadline_days is None or capacity is None
            else float((effort <= inp.deadline_days * capacity).mean())
        ),
        risk_contributions=[
            Contribution(id=item.id, kind=kind, share=round(float(share), 6))
            for (item, kind), share in zip(
                [(t, "task") for t in inp.remaining_tasks] + [(r, "risk") for r in inp.risks], shares, strict=True
            )
        ],
    )
    return envelope(
        result,
        assumptions={
            "n_simulations": inp.n_simulations,
            "seed": seed,
            "quantiles": quantiles,
            "task_distribution": "beta-pert",
            "risks_independent": True,
            "random_streams": "one per task, per risk, and for the daily cost",
            "daily_cost_draw": "one per simulation",
            "cost_budget": inp.cost_budget,
            "deadline_days": inp.deadline_days,
            "team_capacity_days_per_period": capacity,
            "random_generator": GENERATOR,
            "quantile_method": "linear",
        },
        warnings=high_uncertainty(margin),
        method="monte-carlo-beta-pert",
        started=started,
    )


def _check_unique_ids(inp: CompletionInput) -> None:
    """Task and risk ids share one namespace: they label ``risk_contributions``."""
    seen: set[str] = set()
    errors = []
    items = [("remaining_tasks", i, t.id) for i, t in enumerate(inp.remaining_tasks)]
    items += [("risks", i, r.id) for i, r in enumerate(inp.risks)]
    for field, i, id_ in items:
        if id_ in seen:
            errors.append(FieldError((field, i, "id"), f"duplicate id: {id_}"))
        seen.add(id_)
    if errors:
        raise InvalidInput(errors)


def _simulate(inp: CompletionInput, seed: int) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return EAC and remaining effort days per simulation, and each task's and risk's share of cost variance.

    Each task, each risk, and the daily cost has its own random stream, so adding or removing a risk leaves the
    task draws unchanged and the difference between two runs is the effect of that risk alone.
    """
    n = inp.n_simulations
    tasks = [Pert(min=t.optimistic, mode=t.most_likely, max=t.pessimistic) for t in inp.remaining_tasks]
    task_rngs, risk_rngs, (cost_rng,) = make_streams(seed, len(tasks), len(inp.risks), 1)
    eac, effort = np.empty(n), np.empty(n)
    shares = VarianceShares(len(tasks) + len(inp.risks))

    for start, m in chunks(n):
        days = np.empty((len(tasks) + len(inp.risks), m))
        if tasks:
            days[: len(tasks)] = from_normal(tasks, np.stack([rng.standard_normal(m) for rng in task_rngs]))
        for i, (risk, rng) in enumerate(zip(inp.risks, risk_rngs, strict=True)):
            occurs = rng.random(m) < risk.probability
            days[len(tasks) + i] = occurs * from_normal([risk.impact_days], rng.standard_normal((1, m)))[0]
        costs = days * from_normal([inp.daily_cost], cost_rng.standard_normal((1, m)))
        shares.add(costs)
        effort[start : start + m] = days.sum(axis=0)
        eac[start : start + m] = inp.actual_cost + costs.sum(axis=0)

    return eac, effort, shares.shares()
