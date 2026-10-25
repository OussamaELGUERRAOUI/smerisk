"""payroll-at-risk: payroll cost of the coming months, with its variable parts and planned hires (§12)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field

from smerisk.contracts import PayrollBaseline, Payslip, parse_records
from smerisk.core.distributions import Bernoulli, Distribution, Fixed
from smerisk.core.envelope import InputModel, Warning, parse_input
from smerisk.core.errors import MAX_PERIODS, check_limit
from smerisk.core.random import resolve_seed
from smerisk.core.types import Period, Rate
from smerisk.formulas.simulation import AggregateResult, simulate_aggregate
from smerisk.prepare import (
    DerivedParameters,
    QualityReport,
    correlation_matrix,
    estimate_correlations,
    fit_series,
    monthly_totals,
)
from smerisk.usecases._base import (
    Records,
    SimulationSettings,
    UsecaseResult,
    add_months,
    formula_warnings,
    history_start,
    month_of,
    require,
    respond,
)

FAMILIES = ("overtime", "night", "bonus", "sick_pay")
"""Variable parts of the payroll, simulated on top of the baseline of the payroll engine."""
DEFAULT_CORRELATIONS = {"overtime~sick_pay": 0.3}
"""Overtime covers absences: the two move together."""
DEFAULT_HIRE_PROBABILITY = 0.8
MIN_MONTHS = 12
HIRES_PATH = "components.planned_hires.probability"
CorrelationValue = Annotated[float, Field(ge=-1, le=1)]
FamilyValue = Distribution | dict[Period, Distribution]


class PayrollSettings(SimulationSettings):
    history_months: int = Field(24, ge=1, le=120)
    """Complete months of payslips before the month of ``as_of``; 24 or more lets seasonality be estimated."""


class PayrollAtRiskInput(InputModel):
    payslips: Records = Field(default_factory=list)
    payroll_baseline: Records = Field(default_factory=list)
    """Months to forecast, each with the fixed cost already computed by the payroll engine."""
    settings: PayrollSettings = Field(default_factory=PayrollSettings)


def payroll_at_risk(
    params: PayrollAtRiskInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[AggregateResult]:
    started = time.perf_counter()
    inp = parse_input(PayrollAtRiskInput, params, fields)
    settings = inp.settings
    current = month_of(settings.as_of)
    first, last = history_start(settings.as_of, settings.history_months), add_months(current, -1)
    payslips = parse_records(Payslip, inp.payslips, name="payslips")
    baseline = parse_records(PayrollBaseline, inp.payroll_baseline, name="payroll_baseline")
    slips = [s for s in payslips.records if first <= s.period <= last]
    months_ahead = sorted({b.period: b for b in baseline.records if b.period > current}.items())
    require("payslips", len(slips), f"no payslip in the {settings.history_months} months before as_of")
    require("payroll_baseline", len(months_ahead), "no month after as_of")
    check_limit("payroll_baseline", len(months_ahead), MAX_PERIODS)
    periods = [p for p, _ in months_ahead]
    quality = QualityReport()
    quality.records("payslips", payslips, (s.period for s in slips))
    quality.records("payroll_baseline", baseline, periods)
    quality.require("payslips.months", MIN_MONTHS, len({s.period for s in slips}))
    quality.require("payslips.by_family", 1, sum(s.by_family is not None for s in slips))

    derived = DerivedParameters()
    warnings: list[Warning] = []
    series: dict[str, dict[str, float]] = {}
    for family in FAMILIES:
        totals = monthly_totals((s.period, (s.by_family or {}).get(family, 0.0)) for s in slips)
        path = f"components.{family}"
        if not any(totals.values()):
            derived.default(path, Fixed(value=0), f"{family} absent from the payslips", FamilyValue)
            continue
        fit = fit_series(totals, name=family)
        quality.series(f"payslips.{family}", totals, fit, first, last)
        warnings.extend(fit.warnings)
        by_period = {p: fit.distribution(p) for p in periods}
        estimate = fit.estimate(periods[0])
        derived.add(
            path,
            estimate.model_copy(
                update={"value": by_period, "method": f"{estimate.method}; one distribution per month"}
            ),
            FamilyValue,
        )
        series[family] = totals
    pairs, correlation_warnings = estimate_correlations(series, defaults=DEFAULT_CORRELATIONS)
    warnings.extend(correlation_warnings)
    for key, pair in pairs.items():
        derived.add(f"correlation.{key}", pair, CorrelationValue)
    derived.default(HIRES_PATH, DEFAULT_HIRE_PROBABILITY, "probability of a hire without one", Rate)
    derived.apply(settings.overrides)

    components: list[dict[str, Any]] = [
        {"id": "fixed_cost", "sign": 1, "by_period": {p: Fixed(value=b.fixed_cost) for p, b in months_ahead}}
    ]
    grouped = []
    for family in FAMILIES:
        value = derived.value(f"components.{family}")
        by_period = value if isinstance(value, dict) else dict.fromkeys(periods, value)
        if all(isinstance(d, Fixed) and d.value == 0 for d in by_period.values()):
            continue
        components.append({"id": family, "sign": 1, "by_period": by_period, "correlation_group": family})
        grouped.append(family)
    hires_override = derived[HIRES_PATH].source == "override"
    for period, base in months_ahead:
        for i, hire in enumerate(base.planned_hires):
            given = hire.probability is not None and not hires_override
            p = hire.probability if given and hire.probability is not None else derived.value(HIRES_PATH)
            active = {q: Bernoulli(p=p, value=hire.monthly_cost) for q in periods if q >= hire.start_period}
            if active:
                components.append({"id": f"hire.{period}.{i + 1}", "sign": 1, "persistent": True, "by_period": active})
    correlation = None
    if len(grouped) > 1:
        values = {key[len("correlation.") :]: derived.value(key) for key in derived if key.startswith("correlation.")}
        correlation = correlation_matrix(grouped, values)

    seed = resolve_seed(settings.seed)
    formula = simulate_aggregate(
        periods=periods,
        components=components,
        correlation=correlation,
        n_simulations=settings.n_simulations,
        seed=seed,
        confidence=settings.confidence,
        quantiles=settings.quantiles,
        orientation="lower_is_better",
    )
    return respond(
        formula.result,
        method="payroll-at-risk",
        started=started,
        settings=settings,
        assumptions={
            **formula.assumptions,
            "payslip_months": [first, last],
            "families": list(FAMILIES),
            "hires": "one draw for the whole horizon: a hire happens for all its months or not at all",
            "cfar": "cost above the mean at the confidence level, cumulated over the horizon",
        },
        warnings=[*warnings, *formula_warnings(formula)],
        derived=derived,
        quality=quality,
    )
