"""utilization-forecast: billable utilization and bench of a consulting team over the coming months (§12)."""

from __future__ import annotations

import calendar
import datetime as dt
import statistics
import time
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from smerisk.contracts import Consultant, Deal, Mission, Timesheet, parse_records
from smerisk.core.distributions import Distribution, Fixed
from smerisk.core.envelope import InputModel, parse_input
from smerisk.core.random import resolve_seed
from smerisk.core.types import Rate
from smerisk.formulas.bayes import beta_binomial
from smerisk.formulas.utilization import UtilizationResult
from smerisk.formulas.utilization import utilization_forecast as simulate_utilization
from smerisk.prepare import (
    DerivedParameters,
    Estimate,
    QualityReport,
    RateCounts,
    RatePrior,
    renewal_rates,
    stage_rates,
)
from smerisk.prepare.rates import RENEWAL_PRIOR_MEAN, RENEWAL_PRIOR_STRENGTH
from smerisk.usecases._base import (
    Records,
    SimulationSettings,
    UsecaseResult,
    add_months,
    formula_warnings,
    month_of,
    require,
    respond,
)

DEFAULT_RENEWAL_MONTHS = 3.0
MIN_RENEWALS = 5
MIN_ENDED_MISSIONS = 10
BILLABLE_HISTORY_MONTHS = 6
_EDGE = 1e-6
"""``beta_binomial`` takes a prior mean strictly between 0 and 1."""


class UtilizationSettings(SimulationSettings):
    horizon: int = Field(6, ge=1, le=60)
    """Months forecast, from the month after ``as_of``."""
    utilization_threshold: Rate | None = None
    """Break-even utilization: the probability of falling below it is returned."""
    renewal_prior: RatePrior = Field(
        default_factory=lambda: RatePrior(mean=RENEWAL_PRIOR_MEAN, strength=RENEWAL_PRIOR_STRENGTH)
    )
    stages: list[str] | None = None
    """Pipeline stages in order; by default inferred from the deals' histories."""


class UtilizationForecastInput(InputModel):
    consultants: Records = Field(default_factory=list)
    missions: Records = Field(default_factory=list)
    timesheets: Records = Field(default_factory=list)
    deals: Records = Field(default_factory=list)
    settings: UtilizationSettings = Field(default_factory=UtilizationSettings)


def _workdays(month: str) -> int:
    year, number = int(month[:4]), int(month[5:])
    days = calendar.monthrange(year, number)[1]
    return sum(dt.date(year, number, d).weekday() < 5 for d in range(1, days + 1))


def _posterior(counts: RateCounts) -> dict[str, float]:
    prior = counts.prior or RatePrior(mean=0.5, strength=2)
    posterior = beta_binomial(
        successes=counts.successes,
        trials=counts.trials,
        prior={"mean": min(max(prior.mean, _EDGE), 1 - _EDGE), "strength": prior.strength},
    ).result.posterior
    return posterior.model_dump()


def _end(mission: Mission, as_of: dt.date) -> dt.date:
    """Actual end, else planned end; a mission past its planned end and not closed runs at least until ``as_of``."""
    return mission.actual_end or max(mission.planned_end, as_of)


def _months_between(start: dt.date, end: dt.date) -> float:
    return (end - start).days / (365.25 / 12)


def utilization_forecast(
    params: UtilizationForecastInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[UtilizationResult]:
    started = time.perf_counter()
    inp = parse_input(UtilizationForecastInput, params, fields)
    settings = inp.settings
    as_of, current_month = settings.as_of, month_of(settings.as_of)
    consultants = parse_records(Consultant, inp.consultants, name="consultants")
    missions = parse_records(Mission, inp.missions, name="missions")
    timesheets = parse_records(Timesheet, inp.timesheets, name="timesheets")
    deals = parse_records(Deal, inp.deals, name="deals")
    require("consultants", len(consultants.records))
    quality = QualityReport()
    quality.records("consultants", consultants)
    quality.records("missions", missions, (m.start.isoformat() for m in missions.records if m.start <= as_of))
    quality.records("timesheets", timesheets, (t.period for t in timesheets.records if t.period <= current_month))
    quality.records("deals", deals)
    known_missions = [m for m in missions.records if m.start <= as_of]
    periods = [add_months(current_month, i) for i in range(1, settings.horizon + 1)]

    # Billable days per month: per consultant and mission, and for the team, over the last 6 months.
    since = add_months(current_month, -BILLABLE_HISTORY_MONTHS)
    recent = [t for t in timesheets.records if since <= t.period < current_month and t.billable_days > 0]
    by_mission: dict[tuple[str, str | None], list[float]] = defaultdict(list)
    for t in recent:
        by_mission[(t.consultant_ref, t.mission_ref)].append(t.billable_days)
    leave = defaultdict(float, {(t.consultant_ref, t.period): t.leave_days for t in timesheets.records})
    derived = DerivedParameters()
    if recent:
        derived.add(
            "billable_days_per_period",
            Estimate(
                value=statistics.mean(t.billable_days for t in recent),
                source="estimated",
                method=f"mean billable days per consultant-month over the last {BILLABLE_HISTORY_MONTHS} months",
                n_points=len(recent),
                first=min(t.period for t in recent),
                last=max(t.period for t in recent),
            ),
            float,
        )
    else:
        derived.default("billable_days_per_period", float(_workdays(periods[0])), "working days of the month", float)

    company, customers = renewal_rates(known_missions, as_of, prior=settings.renewal_prior)
    quality.require("missions.ended", MIN_ENDED_MISSIONS, company.n_points)
    renewal_lengths = [
        _months_between(m.start, m.actual_end or m.planned_end)
        for m in known_missions
        if m.renewal_of is not None and (m.actual_end or m.planned_end) <= as_of
    ]
    if len(renewal_lengths) >= MIN_RENEWALS:
        derived.add(
            "renewal_duration_months",
            Estimate(
                value=Fixed(value=max(1.0, round(statistics.median(renewal_lengths)))),
                source="estimated",
                method="median length of past renewals",
                n_points=len(renewal_lengths),
            ),
            Distribution,
        )
    else:
        derived.default(
            "renewal_duration_months",
            Fixed(value=DEFAULT_RENEWAL_MONTHS),
            f"fewer than {MIN_RENEWALS} past renewals",
            Distribution,
        )

    # Current mission of each consultant: the one under way at as_of that ends last.
    current: dict[str, Mission] = {}
    for m in sorted(known_missions, key=lambda m: _end(m, as_of)):
        if _end(m, as_of) >= as_of:
            current[m.consultant_ref] = m  # sorted by end: the one that ends last wins
    for c in consultants.records:
        ref = c.consultant_ref
        available = c.available_days or {p: max(0.0, _workdays(p) - leave[(ref, p)]) for p in periods}
        derived.add(
            f"consultants.{ref}.available_days",
            Estimate(
                value={p: available.get(p, 0.0) for p in periods},
                source="estimated",
                method="working days minus planned leave"
                if c.available_days is None
                else "consultants.available_days (a month absent counts 0)",
            ),
            dict[str, float],
        )
        mission = current.get(ref)
        if mission is None:
            continue
        counts = customers[mission.customer_ref] if mission.customer_ref in customers else company
        derived.add(
            f"consultants.{ref}.renewal_probability_beta",
            counts.model_copy(update={"value": _posterior(counts.value)}),
            dict[str, float],
        )
        days = by_mission.get((ref, mission.mission_ref))
        if days:
            derived.add(
                f"consultants.{ref}.billable_days_per_period",
                Estimate(
                    value=statistics.mean(days),
                    source="estimated",
                    method="timesheets of the mission",
                    n_points=len(days),
                ),
                float,
            )

    stages = stage_rates(deals.records, settings.stages)
    open_deals = [d for d in deals.records if d.outcome == "OPEN"]
    usable_deals = [d for d in open_deals if d.expected_start is not None and d.expected_duration_months is not None]
    quality.require("deals.open_with_start_and_duration", len(open_deals), len(usable_deals))
    for d in usable_deals:
        posterior = _posterior(stages[d.stage].value)
        derived.add(
            f"deals.{d.deal_ref}.win_probability",
            stages[d.stage].model_copy(update={"value": posterior["alpha"] / (posterior["alpha"] + posterior["beta"])}),
            Rate,
        )
    derived.apply(settings.overrides)

    team_days = derived.value("billable_days_per_period")
    formula_consultants = []
    for c in consultants.records:
        ref = c.consultant_ref
        item: dict[str, Any] = {"id": ref, "available_days": derived.value(f"consultants.{ref}.available_days")}
        mission = current.get(ref)
        if mission is not None:
            item["current_mission"] = {
                "end_period": month_of(_end(mission, as_of)),
                "billable_days_per_period": derived.value(f"consultants.{ref}.billable_days_per_period")
                if f"consultants.{ref}.billable_days_per_period" in derived
                else team_days,
                "renewal_probability_beta": derived.value(f"consultants.{ref}.renewal_probability_beta"),
                "renewal_duration_months": derived.value("renewal_duration_months"),
            }
        formula_consultants.append(item)
    formula_deals = [
        {
            "id": d.deal_ref,
            "win_probability": derived.value(f"deals.{d.deal_ref}.win_probability"),
            "start_period": max(month_of(d.expected_start), periods[0]) if d.expected_start else periods[0],
            "consultants_needed": d.consultants_needed,
            "billable_days_per_period": team_days,
            "duration_months": d.expected_duration_months,
        }
        for d in usable_deals
    ]
    seed = resolve_seed(settings.seed)
    formula = simulate_utilization(
        periods=periods,
        consultants=formula_consultants,
        deals=formula_deals,
        utilization_threshold=settings.utilization_threshold,
        n_simulations=settings.n_simulations,
        seed=seed,
        quantiles=settings.quantiles,
    )
    return respond(
        formula.result,
        method="utilization-forecast",
        started=started,
        settings=settings,
        assumptions={
            **formula.assumptions,
            "periods": periods,
            "deal_start": "expected start, not before the first month",
        },
        warnings=formula_warnings(formula),
        derived=derived,
        quality=quality,
    )
