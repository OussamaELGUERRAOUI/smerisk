"""Payslip, AbsenceMonth, EmploymentPeriod, PayrollBaseline (§10.1)."""

from __future__ import annotations

import datetime as dt
from typing import Literal, Self

from pydantic import Field, model_validator

from smerisk.contracts._base import Record, Ref
from smerisk.core.types import Month, Rate

PayFamily = Literal[
    "base", "overtime", "night", "bonus", "benefits", "paid_leave", "termination", "sick_pay", "back_pay", "other"
]

EndReason = Literal[
    "RESIGNATION", "END_OF_FIXED_TERM", "TRIAL_TERMINATION", "DISMISSAL", "MUTUAL_AGREEMENT", "RETIREMENT", "OTHER"
]


class Hours(Record):
    paid: float | None = Field(None, ge=0)
    overtime: float | None = Field(None, ge=0)
    night: float | None = Field(None, ge=0)


class Payslip(Record):
    """One validated payslip."""

    employee_ref: Ref
    period: Month
    site_ref: Ref | None = None
    group: Ref | None = None
    gross: float
    """Can be negative on a corrective payslip."""
    employer_contributions: float
    """Net of reductions."""
    employer_cost: float
    """Gross + employer contributions − daily sickness benefits received by subrogation."""
    by_family: dict[PayFamily, float] | None = None
    hours: Hours | None = None


class AbsenceMonth(Record):
    """Absences aggregated by month (and group), never by employee."""

    period: Month
    group: Ref | None = None
    """A group of fewer than 3 employees is merged into "others" during preparation."""
    absence_days: float = Field(ge=0)
    theoretical_days: float = Field(gt=0)
    headcount: int = Field(ge=1)
    """Average headcount for the month."""

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if self.absence_days > self.theoretical_days:
            raise ValueError("absence_days cannot exceed theoretical_days")
        return self


class EmploymentGroups(Record):
    qualification: Ref | None = None
    site_ref: Ref | None = None
    contract_type: Ref | None = None


class EmploymentPeriod(Record):
    """One period of employment, or one contract when ``contracts`` is sent instead (merged during preparation)."""

    employee_ref: Ref
    start: dt.date
    end: dt.date | None = None
    """``None`` while the employee is still present."""
    end_reason: EndReason | None = None
    """Only with ``end``; a missing reason is read as ``OTHER``."""
    groups: EmploymentGroups | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if self.end is None:
            if self.end_reason is not None:
                raise ValueError("end_reason requires an end date")
        elif self.end < self.start:
            raise ValueError("end is before start")
        return self


Contract = EmploymentPeriod
"""A single contract; successive contracts at most one day apart form one employment period."""


class PlannedHire(Record):
    start_period: Month
    monthly_cost: float = Field(ge=0)
    probability: Rate | None = None
    """``None``: the default of the use case (0.8) is applied and reported as a default."""


class PayrollBaseline(Record):
    """The deterministic forecast already computed by the payroll engine, one element per month."""

    period: Month
    fixed_cost: float = Field(ge=0)
    """Employer cost of the planned fixed items."""
    planned_hires: list[PlannedHire] = Field(default_factory=list)
