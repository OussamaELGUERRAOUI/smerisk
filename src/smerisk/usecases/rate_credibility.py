"""rate-credibility: absenteeism or turnover rate of each group, made credible with Bühlmann-Straub (§12)."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field

from smerisk.contracts import AbsenceMonth, EmploymentPeriod, parse_records
from smerisk.core.envelope import InputModel, parse_input
from smerisk.core.errors import FieldError, InvalidInput
from smerisk.core.types import Rate, month_range
from smerisk.formulas.bayes import CredibilityResult, credibility
from smerisk.prepare import DerivedParameters, Estimate, QualityReport, merge_contracts
from smerisk.prepare.employment import GroupBy, monthly_turnover
from smerisk.usecases._base import (
    Records,
    Settings,
    UsecaseResult,
    add_months,
    formula_warnings,
    history_start,
    merge_small_groups,
    month_of,
    require,
    respond,
)

MIN_GROUPS_TO_ESTIMATE = 5
MIN_MONTHS = 12
PUBLISHER_CONSTANTS = {"absenteeism": 4_000.0, "turnover": 600.0}
"""Credibility constant k used with fewer than 5 groups, in units of exposure: a group with k theoretical days
(absenteeism) or k employee-months (turnover) gets a credibility of 0.5.

Absenteeism: a daily absence rate around 5%, clustered in long absences (variance about 20 times the binomial one),
and true rates differing by about 1.5 points between sites give k ≈ 0.95 / 0.015² ≈ 4,000 days, about 18
full-time employees over a year. Turnover: a monthly exit rate around 1.5% and true rates differing by about 0.5
point give k ≈ 0.015 / 0.005² ≈ 600 employee-months, 50 employees over a year."""


class RateCredibilitySettings(Settings):
    indicator: Literal["absenteeism", "turnover"] = "absenteeism"
    history_months: int = Field(24, ge=1, le=120)
    """Complete months before the month of ``as_of``."""
    group_by: GroupBy | None = None
    """Turnover only: the field of ``employment_periods.groups`` that defines the groups."""
    reference_mean: Rate | None = None
    """Reference rate (sector, company); required with a single group."""
    credibility_constant: float | None = Field(None, gt=0)


class RateCredibilityInput(InputModel):
    absence_months: Records = Field(default_factory=list)
    employment_periods: Records = Field(default_factory=list)
    settings: RateCredibilitySettings = Field(default_factory=RateCredibilitySettings)


def _absenteeism(
    rows: Records, months: list[str], quality: QualityReport
) -> tuple[dict[str, dict[str, tuple[float, float]]], int]:
    """Group → month → (absence days, theoretical days), and the number of records used."""
    parsed = parse_records(AbsenceMonth, rows, name="absence_months")
    records = [r for r in parsed.records if months[0] <= r.period <= months[-1]]
    quality.records("absence_months", parsed, (r.period for r in records))
    headcounts: dict[str, list[int]] = defaultdict(list)
    for r in records:
        headcounts[r.group or "ALL"].append(r.headcount)
    names = merge_small_groups({g: sum(h) / len(h) for g, h in headcounts.items()})
    out: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in records:
        group = names[r.group or "ALL"]
        absent, theoretical = out[group].get(r.period, (0.0, 0.0))
        out[group][r.period] = (absent + r.absence_days, theoretical + r.theoretical_days)
    return dict(out), len(records)


def _turnover(
    rows: Records, months: list[str], settings: RateCredibilitySettings, quality: QualityReport
) -> tuple[dict[str, dict[str, tuple[float, float]]], int]:
    """Group → month → (exits, employee-months), and the number of periods used."""
    parsed = parse_records(EmploymentPeriod, rows, name="employment_periods")
    periods = merge_contracts(parsed.records)
    quality.records("employment_periods", parsed, (p.start.isoformat() for p in periods))
    panel = monthly_turnover(periods, months, group_by=settings.group_by)
    names = merge_small_groups(
        {g: sum(m.headcount for m in by_month.values()) / len(months) for g, by_month in panel.items()}
    )
    out: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for group, by_month in panel.items():
        for month, m in by_month.items():
            exits, exposure = out[names[group]].get(month, (0.0, 0.0))
            out[names[group]][month] = (exits + m.exits, exposure + m.exposure)
    return dict(out), len(periods)


def rate_credibility(
    params: RateCredibilityInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[CredibilityResult]:
    started = time.perf_counter()
    inp = parse_input(RateCredibilityInput, params, fields)
    settings = inp.settings
    months = month_range(
        history_start(settings.as_of, settings.history_months), add_months(month_of(settings.as_of), -1)
    )
    quality = QualityReport()
    if settings.indicator == "absenteeism":
        counts, used = _absenteeism(inp.absence_months, months, quality)
        contract, unit = "absence_months", "absence days / theoretical days"
    else:
        counts, used = _turnover(inp.employment_periods, months, settings, quality)
        contract, unit = "employment_periods", "exits / employee-months"
    require(contract, used, f"no {contract} record in the {settings.history_months} months before as_of")

    observations = {
        group: [
            {"value": n / exposure, "weight": exposure} for _, (n, exposure) in sorted(by_month.items()) if exposure
        ]
        for group, by_month in sorted(counts.items())
    }
    groups: list[dict[str, Any]] = [{"id": g, "observations": obs} for g, obs in observations.items() if obs]
    require(contract, len(groups), "no exposure in the period")
    n_months = len({m for by_month in counts.values() for m in by_month})
    quality.require(f"{contract}.months", MIN_MONTHS, n_months)

    derived = DerivedParameters()
    derived.add(
        "groups",
        Estimate(
            value=groups,
            source="estimated",
            method=f"monthly rate = {unit}, weighted by the denominator",
            n_points=sum(map(len, observations.values())),
            first=months[0],
            last=months[-1],
        ),
        list[dict[str, Any]],
    )
    numerator = sum(n for by_month in counts.values() for n, _ in by_month.values())
    overall = numerator / sum(e for by_month in counts.values() for _, e in by_month.values())
    estimate_k = settings.credibility_constant is None and len(groups) >= MIN_GROUPS_TO_ESTIMATE
    if not estimate_k:
        if settings.reference_mean is not None:
            derived.add(
                "reference_mean", Estimate(value=settings.reference_mean, source="override", method="set by the caller")
            )
        elif len(groups) >= 2:
            derived.add(
                "reference_mean",
                Estimate(value=overall, source="estimated", method="rate of all groups together", n_points=len(groups)),
            )
        else:
            raise InvalidInput([FieldError(("settings", "reference_mean"), "required with a single group")])
        if settings.credibility_constant is not None:
            derived.add(
                "credibility_constant",
                Estimate(value=settings.credibility_constant, source="override", method="set by the caller"),
            )
        else:
            derived.default(
                "credibility_constant",
                PUBLISHER_CONSTANTS[settings.indicator],
                reason=f"publisher value for {settings.indicator}: fewer than {MIN_GROUPS_TO_ESTIMATE} groups",
            )
    derived.apply(settings.overrides)
    formula = credibility(
        mode="estimate" if estimate_k else "reference",
        groups=derived.value("groups"),
        reference_mean=None if estimate_k else derived.value("reference_mean"),
        credibility_constant=None if estimate_k else derived.value("credibility_constant"),
        min_groups=MIN_GROUPS_TO_ESTIMATE,
    )
    return respond(
        formula.result,
        method="rate-credibility",
        started=started,
        settings=settings,
        assumptions={**formula.assumptions, "rate": unit, "months": [months[0], months[-1]]},
        warnings=formula_warnings(formula),
        derived=derived,
        quality=quality,
    )
