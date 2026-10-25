"""Combine contracts, events, and censoring records (§11.3)."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Literal

from pydantic import BaseModel

from smerisk.contracts import EmploymentPeriod
from smerisk.prepare._base import Estimate

MERGE_GAP_DAYS = 1
"""Successive contracts at most one day apart form one period of employment."""
DAYS_PER_MONTH = 365.25 / 12
DEFAULT_EXCLUDED_REASONS = ("END_OF_FIXED_TERM", "RETIREMENT")
UNKNOWN_GROUP = "unknown"

GroupBy = Literal["qualification", "site_ref", "contract_type"]


class SurvivalSubject(BaseModel):
    """Input of ``kaplan_meier``."""

    duration: float
    """Months of employment, up to the exit or the analysis date."""
    event: bool
    group: str | None = None


def merge_contracts(contracts: Sequence[EmploymentPeriod]) -> list[EmploymentPeriod]:
    """Merge each employee's successive or overlapping contracts into periods of employment.

    A merged period keeps the end reason and the groups of its last contract (the situation reached).
    """
    by_employee: dict[str, list[EmploymentPeriod]] = defaultdict(list)
    for c in contracts:
        by_employee[c.employee_ref].append(c)
    out = []
    for employee in sorted(by_employee):
        current: EmploymentPeriod | None = None
        for c in sorted(by_employee[employee], key=lambda c: c.start):
            if current is not None and (current.end is None or (c.start - current.end).days <= MERGE_GAP_DAYS):
                # Extend the period, unless this contract lies within it (or within a period still open).
                if current.end is not None and (c.end is None or c.end >= current.end):
                    current = current.model_copy(
                        update={"end": c.end, "end_reason": c.end_reason, "groups": c.groups or current.groups}
                    )
            else:
                if current is not None:
                    out.append(current)
                current = c
        if current is not None:
            out.append(current)
    return out


def survival_subjects(
    periods: Sequence[EmploymentPeriod],
    as_of: dt.date,
    *,
    exclude_reasons: Collection[str] = DEFAULT_EXCLUDED_REASONS,
    group_by: GroupBy | None = None,
) -> Estimate[list[SurvivalSubject]]:
    """Durations and events for ``kaplan_meier``.

    An exit is an event unless its reason is in ``exclude_reasons``: the end of a fixed-term contract or a retirement
    is then a censoring at the exit date. A period still open at ``as_of`` (or ending after it) is censored at
    ``as_of``; a period starting after ``as_of`` is left out. An exit without a reason counts as ``OTHER``.
    """
    subjects = []
    starts = []
    for p in periods:
        if p.start > as_of:
            continue
        ended = p.end is not None and p.end <= as_of
        end = p.end if ended and p.end is not None else as_of
        reason = p.end_reason or "OTHER"
        group = None
        if group_by is not None:
            group = (getattr(p.groups, group_by) if p.groups is not None else None) or UNKNOWN_GROUP
        subjects.append(
            SurvivalSubject(
                duration=(end - p.start).days / DAYS_PER_MONTH,
                event=ended and reason not in exclude_reasons,
                group=group,
            )
        )
        starts.append(p.start.isoformat())
    excluded = ", ".join(sorted(exclude_reasons)) or "none"
    return Estimate(
        value=subjects,
        source="estimated",
        method=f"months of employment; exits for {excluded} censored",
        n_points=len(subjects),
        first=min(starts) if starts else None,
        last=as_of.isoformat(),
    )


class MonthlyTurnover(BaseModel):
    exposure: float
    """Employee-months present during the month (an employee present half the month counts 0.5)."""
    exits: int
    headcount: int
    """Employees present at least one day."""


def monthly_turnover(
    periods: Sequence[EmploymentPeriod],
    months: Sequence[str],
    *,
    exclude_reasons: Collection[str] = DEFAULT_EXCLUDED_REASONS,
    group_by: GroupBy | None = None,
) -> dict[str, dict[str, MonthlyTurnover]]:
    """Exposure and exits by group and month, for a monthly turnover rate = exits / exposure.

    An exit counts in the month of its end date, unless its reason is in ``exclude_reasons``. Without ``group_by``,
    every period is in the group ``ALL``.
    """
    out: dict[str, dict[str, MonthlyTurnover]] = defaultdict(dict)
    for month in months:
        year, number = int(month[:4]), int(month[5:])
        first = dt.date(year, number, 1)
        last = dt.date(year + number // 12, number % 12 + 1, 1) - dt.timedelta(days=1)
        days = (last - first).days + 1
        acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0, 0])
        for p in periods:
            start, end = max(p.start, first), min(p.end or last, last)
            if end < start:
                continue
            group = "ALL"
            if group_by is not None:
                group = (getattr(p.groups, group_by) if p.groups is not None else None) or UNKNOWN_GROUP
            a = acc[group]
            a[0] += ((end - start).days + 1) / days
            a[2] += 1
            if p.end is not None and first <= p.end <= last and (p.end_reason or "OTHER") not in exclude_reasons:
                a[1] += 1
        for group, (exposure, exits, headcount) in acc.items():
            out[group][month] = MonthlyTurnover(exposure=exposure, exits=int(exits), headcount=int(headcount))
    return dict(out)
