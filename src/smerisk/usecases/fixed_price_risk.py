"""fixed-price-risk: final cost, margin, budget, and deadline of each fixed-price project (§12)."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Mapping
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from smerisk.contracts import Consultant, FixedPriceProject, RemainingTask, Timesheet, parse_records
from smerisk.core.distributions import Distribution, Fixed, Pert
from smerisk.core.envelope import InputModel, Warning, parse_input
from smerisk.core.random import resolve_seed
from smerisk.formulas.projects import CompletionResult, fixed_price_completion
from smerisk.prepare import DerivedParameters, Estimate, QualityReport, actual_cost, team_daily_cost
from smerisk.usecases._base import Records, SimulationSettings, UsecaseResult, month_of, require, respond


class FixedPriceRiskInput(InputModel):
    fixed_price_projects: Records = Field(default_factory=list)
    consultants: Records = Field(default_factory=list)
    timesheets: Records = Field(default_factory=list)
    """Days spent on each project (``mission_ref`` = ``project_ref``); otherwise ``actual_days`` of the project."""
    settings: SimulationSettings = Field(default_factory=SimulationSettings)


class ProjectRisk(BaseModel):
    project_ref: str
    deadline_days: float | None
    """Working days from ``as_of`` to the deadline; ``None`` when the deadline has passed."""
    completion: CompletionResult


class FixedPriceRiskResult(BaseModel):
    projects: list[ProjectRisk]


def _task_or_certain_risk(task: RemainingTask) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A task whose three estimates are equal has no spread: it becomes a certain risk of fixed impact."""
    if task.optimistic < task.pessimistic:
        return {
            "id": task.task_ref,
            "optimistic": task.optimistic,
            "most_likely": task.most_likely,
            "pessimistic": task.pessimistic,
        }, None
    return None, {"id": task.task_ref, "probability": 1.0, "impact_days": Fixed(value=task.most_likely)}


def _working_days(start: dt.date, end: dt.date) -> int:
    """Working days after ``start`` up to ``end``, inclusive."""
    return int(np.busday_count(start + dt.timedelta(days=1), end + dt.timedelta(days=1)))


def fixed_price_risk(
    params: FixedPriceRiskInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[FixedPriceRiskResult]:
    started = time.perf_counter()
    inp = parse_input(FixedPriceRiskInput, params, fields)
    settings = inp.settings
    projects = parse_records(FixedPriceProject, inp.fixed_price_projects, name="fixed_price_projects")
    consultants = parse_records(Consultant, inp.consultants, name="consultants")
    timesheets = parse_records(Timesheet, inp.timesheets, name="timesheets")
    require("fixed_price_projects", len(projects.records))
    require("consultants", len(consultants.records))
    quality = QualityReport()
    quality.records("fixed_price_projects", projects)
    quality.records("consultants", consultants)
    month = month_of(settings.as_of)
    quality.records("timesheets", timesheets, (t.period for t in timesheets.records if t.period <= month))

    derived = DerivedParameters()
    planned: list[tuple[FixedPriceProject, str, int]] = []
    for project in projects.records:
        base = f"projects.{project.project_ref}"
        daily, missing = team_daily_cost(project, consultants.records)
        quality.require(f"{base}.team_costs", len(project.team), len(project.team) - len(missing))
        if daily is None:
            continue
        spent = actual_cost(project, consultants.records, timesheets.records, until=month)
        quality.require(f"{base}.actual_cost", 1, int(spent is not None))
        tasks, risks = [], []
        for task in project.remaining_tasks:
            as_task, as_risk = _task_or_certain_risk(task)
            tasks += [as_task] if as_task else []
            risks += [as_risk] if as_risk else []
        for r in project.risks:
            impact: Distribution = (
                Pert(min=0, mode=r.impact_days_most_likely, max=r.impact_days_max)
                if r.impact_days_max > 0
                else Fixed(value=0)
            )
            risks.append({"id": r.risk_ref, "probability": r.probability, "impact_days": impact})
        derived.add(f"{base}.daily_cost", daily, Distribution)
        derived.add(
            f"{base}.actual_cost",
            spent or Estimate(value=0.0, source="default", method="no timesheet and no actual_days: 0"),
            float,
        )
        derived.add(
            f"{base}.remaining_tasks",
            Estimate(value=tasks, source="estimated", method="remaining tasks (days)", n_points=len(tasks)),
            list[dict[str, Any]],
        )
        derived.add(
            f"{base}.risks",
            Estimate(value=risks, source="estimated", method="risk register; impact PERT(0, most likely, max)"),
            list[dict[str, Any]],
        )
        planned.append((project, base, len(project.team) - len(missing)))
    require("consultants", len(planned), "no project has a team member with a known daily cost")
    derived.apply(settings.overrides)

    seed = resolve_seed(settings.seed)
    results = []
    warnings: list[Warning] = []
    assumptions: dict[str, Any] = {"seed": seed}
    for project, base, members in planned:
        deadline = _working_days(settings.as_of, project.deadline)
        completion = fixed_price_completion(
            contract_price=project.price,
            actual_cost=derived.value(f"{base}.actual_cost"),
            cost_budget=project.cost_budget,
            remaining_tasks=derived.value(f"{base}.remaining_tasks"),
            risks=derived.value(f"{base}.risks"),
            daily_cost=derived.value(f"{base}.daily_cost"),
            deadline_days=deadline if deadline > 0 else None,
            team_capacity_days_per_period=members if deadline > 0 else None,
            n_simulations=settings.n_simulations,
            seed=seed,
            quantiles=settings.quantiles,
        )
        warnings += [Warning(code=w.code, message=f"{project.project_ref}: {w.message}") for w in completion.warnings]
        assumptions["fixed_price_completion"] = {k: v for k, v in completion.assumptions.items() if k != "seed"}
        results.append(
            ProjectRisk(
                project_ref=project.project_ref,
                deadline_days=deadline if deadline > 0 else None,
                completion=completion.result,
            )
        )
    assumptions["team_capacity"] = "one day of effort per working day for each member with a known cost"
    return respond(
        FixedPriceRiskResult(projects=results),
        method="fixed-price-risk",
        started=started,
        settings=settings,
        assumptions=assumptions,
        warnings=warnings,
        derived=derived,
        quality=quality,
    )
