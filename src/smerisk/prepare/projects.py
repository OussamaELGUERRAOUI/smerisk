"""Daily cost of a team and cost already spent on a fixed-price project (§11.3)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from smerisk.contracts import Consultant, FixedPriceProject, Timesheet
from smerisk.core.distributions import Distribution, Fixed, Normal
from smerisk.prepare._base import Estimate


def team_daily_cost(
    project: FixedPriceProject, consultants: Sequence[Consultant]
) -> tuple[Estimate[Distribution] | None, list[str]]:
    """Mean daily cost of the team, weighted by planned days, with the spread between members.

    Equal weights unless every member has ``planned_days``. Returns the estimate (``None`` if no member is found)
    and the members missing from ``consultants``.
    """
    costs = {c.consultant_ref: c.daily_cost for c in consultants}
    members = [m for m in project.team if m.consultant_ref in costs]
    missing = [m.consultant_ref for m in project.team if m.consultant_ref not in costs]
    if not members:
        return None, missing
    weighted = all(m.planned_days is not None for m in members) and sum(m.planned_days or 0 for m in members) > 0
    w = np.array([m.planned_days if weighted and m.planned_days is not None else 1.0 for m in members])
    c = np.array([costs[m.consultant_ref] for m in members])
    mean = float(np.average(c, weights=w))
    sd = float(np.sqrt(np.average((c - mean) ** 2, weights=w)))
    dist: Distribution = Normal(mean=mean, sd=sd, min=0) if sd > 0 else Fixed(value=mean)
    weights = "planned days" if weighted else "equal weights"
    estimate: Estimate[Distribution] = Estimate(
        value=dist,
        source="estimated",
        method=f"team mean daily cost ({weights}), spread between members",
        n_points=len(members),
    )
    return estimate, missing


def actual_cost(
    project: FixedPriceProject,
    consultants: Sequence[Consultant],
    timesheets: Sequence[Timesheet] = (),
    *,
    until: str | None = None,
) -> Estimate[float] | None:
    """Cost already spent: days of the timesheets on the project (``mission_ref`` = ``project_ref``) at each
    consultant's daily cost, up to the month ``until``; without timesheets, ``actual_days`` at the team's mean cost.

    ``None`` when neither is available, or when no consultant's cost is known.
    """
    costs = {c.consultant_ref: c.daily_cost for c in consultants}
    lines = [
        t
        for t in timesheets
        if t.mission_ref == project.project_ref and (until is None or t.period <= until) and t.billable_days > 0
    ]
    if lines:
        known = [t for t in lines if t.consultant_ref in costs]
        if not known:
            return None
        periods = sorted(t.period for t in known)
        return Estimate(
            value=round(sum(t.billable_days * costs[t.consultant_ref] for t in known), 2),
            source="estimated",
            method="timesheet days × daily cost of each consultant",
            n_points=len(known),
            first=periods[0],
            last=periods[-1],
        )
    if project.actual_days is None:
        return None
    team, _ = team_daily_cost(project, consultants)
    if team is None:
        return None
    mean = (
        team.value.value if isinstance(team.value, Fixed) else team.value.mean if isinstance(team.value, Normal) else 0
    )
    return Estimate(
        value=round(project.actual_days * mean, 2),
        source="estimated",
        method="actual_days × mean daily cost of the team",
        n_points=1,
    )
