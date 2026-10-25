"""Timesheet, Mission, Consultant, Deal, FixedPriceProject (§10.3)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from smerisk.contracts._base import Days, Record, Ref
from smerisk.core.types import Month, Rate

BillingType = Literal["TIME", "FIXED_PRICE", "WORK_UNITS", "RECURRING"]
Outcome = Literal["OPEN", "WON", "LOST"]


class Timesheet(Record):
    """Monthly activity report, by consultant, mission, and month."""

    consultant_ref: Ref
    period: Month
    mission_ref: Ref | None = None
    """``None`` for non-billable time."""
    billable_days: Days = 0
    non_billable_days: Days = 0
    absence_days: Days = 0
    leave_days: Days = 0

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if self.billable_days > 0 and self.mission_ref is None:
            raise ValueError("billable_days requires a mission_ref")
        if self.billable_days + self.non_billable_days + self.absence_days + self.leave_days > 31:
            raise ValueError("more than 31 days in one month")
        return self


class Mission(Record):
    mission_ref: Ref
    customer_ref: Ref
    consultant_ref: Ref
    start: dt.date
    planned_end: dt.date
    actual_end: dt.date | None = None
    """``None`` while the mission is under way."""
    renewal_of: Ref | None = None
    """The mission that this one extends; used to count renewals."""
    billing_type: BillingType
    daily_rate: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if self.planned_end < self.start:
            raise ValueError("planned_end is before start")
        if self.actual_end is not None and self.actual_end < self.start:
            raise ValueError("actual_end is before start")
        if self.renewal_of == self.mission_ref:
            raise ValueError("a mission cannot renew itself")
        return self


class Consultant(Record):
    consultant_ref: Ref
    daily_cost: float = Field(gt=0)
    """Actual cost per day, from payroll."""
    available_days: dict[Month, Days] | None = None
    """``None``: working days minus planned ``leave_days``."""


class StageChange(Record):
    stage: Ref
    date: dt.date


class Deal(Record):
    deal_ref: Ref
    customer_ref: Ref
    stage: Ref
    """Current stage, or final stage of a closed deal."""
    stages_history: list[StageChange] = Field(default_factory=list)
    amount: float = Field(ge=0)
    outcome: Outcome
    expected_start: dt.date | None = None
    consultants_needed: int = Field(1, ge=1)
    expected_duration_months: float | None = Field(None, gt=0)


class RemainingTask(Record):
    """Remaining work of one task, in days."""

    task_ref: Ref
    optimistic: float = Field(ge=0)
    most_likely: float = Field(ge=0)
    pessimistic: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if not self.optimistic <= self.most_likely <= self.pessimistic:
            raise ValueError("expected optimistic <= most_likely <= pessimistic")
        return self


class ProjectRisk(Record):
    risk_ref: Ref
    probability: Rate
    impact_days_most_likely: float = Field(ge=0)
    impact_days_max: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_impact(self) -> Self:
        if self.impact_days_max < self.impact_days_most_likely:
            raise ValueError("impact_days_max is below impact_days_most_likely")
        return self


class TeamMember(Record):
    consultant_ref: Ref
    planned_days: float | None = Field(None, ge=0)
    """Weight of the member in the daily cost of the team; ``None`` for all members: equal weights."""


class FixedPriceProject(Record):
    project_ref: Ref
    price: float = Field(gt=0)
    cost_budget: float = Field(gt=0)
    deadline: dt.date
    actual_days: float | None = Field(None, ge=0)
    """Days spent so far, when timesheets are not sent."""
    remaining_tasks: list[RemainingTask] = Field(default_factory=list)
    risks: list[ProjectRisk] = Field(default_factory=list)
    team: list[TeamMember] = Field(default_factory=list)
    """Members, or simply their ``consultant_ref``."""

    @field_validator("team", mode="before")
    @classmethod
    def _refs_as_members(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"consultant_ref": m} if isinstance(m, str) else m for m in value]
        return value

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        for name, refs in (
            ("remaining_tasks", [t.task_ref for t in self.remaining_tasks]),
            ("risks", [r.risk_ref for r in self.risks]),
            ("team", [m.consultant_ref for m in self.team]),
        ):
            if len(set(refs)) < len(refs):
                raise ValueError(f"duplicate references in {name}")
        return self
