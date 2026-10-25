"""attrition: employment history to Kaplan-Meier retention curves (§12)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from smerisk.contracts import EmploymentPeriod, parse_records
from smerisk.contracts.payroll import EndReason
from smerisk.core.envelope import InputModel, parse_input
from smerisk.core.types import Level
from smerisk.formulas.survival import KaplanMeierResult, kaplan_meier
from smerisk.prepare import DerivedParameters, QualityReport, merge_contracts, survival_subjects
from smerisk.prepare.employment import GroupBy
from smerisk.usecases._base import (
    MIN_GROUP_HEADCOUNT,
    Records,
    Settings,
    UsecaseResult,
    formula_warnings,
    one_of,
    require,
    respond,
)

_EXCLUDED: tuple[EndReason, ...] = ("END_OF_FIXED_TERM", "RETIREMENT")

MIN_SUBJECTS = 20
"""Below 20 employees the curve moves by 5 points or more at each exit: indicative only."""


class AttritionSettings(Settings):
    group_by: GroupBy | None = None
    time_points: list[float] = Field(default_factory=lambda: [3.0, 6.0, 12.0, 24.0], min_length=1)
    """Months at which the retention rate is reported."""
    exclude_reasons: list[EndReason] = Field(default_factory=lambda: list(_EXCLUDED))
    """Exits that are censored rather than counted as departures."""
    confidence: Level = 0.95


class AttritionInput(InputModel):
    employment_periods: Records = Field(default_factory=list)
    contracts: Records = Field(default_factory=list)
    """Individual contracts instead of periods: successive contracts at most one day apart are merged."""
    settings: AttritionSettings = Field(default_factory=AttritionSettings)


def attrition(
    params: AttritionInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[KaplanMeierResult]:
    started = time.perf_counter()
    inp = parse_input(AttritionInput, params, fields)
    settings = inp.settings
    name, rows = one_of(("employment_periods", inp.employment_periods), ("contracts", inp.contracts))
    parsed = parse_records(EmploymentPeriod, rows, name=name)
    periods = merge_contracts(parsed.records)
    quality = QualityReport()
    quality.records(name, parsed, (p.start.isoformat() for p in periods if p.start <= settings.as_of))

    subjects = survival_subjects(
        periods, settings.as_of, exclude_reasons=settings.exclude_reasons, group_by=settings.group_by
    )
    require(name, len(subjects.value), "no period of employment starts before as_of")
    quality.require(f"{name}.employees", MIN_SUBJECTS, len(subjects.value))

    derived = DerivedParameters()
    derived.add("subjects", subjects.model_copy(update={"value": [s.model_dump() for s in subjects.value]}))
    derived.apply(settings.overrides)
    formula = kaplan_meier(
        subjects=derived.value("subjects"),
        time_points=settings.time_points,
        confidence=settings.confidence,
        min_group_size=MIN_GROUP_HEADCOUNT,
    )
    return respond(
        formula.result,
        method="attrition",
        started=started,
        settings=settings,
        assumptions={**formula.assumptions, "contracts_merged": name == "contracts"},
        warnings=formula_warnings(formula),
        derived=derived,
        quality=quality,
    )
