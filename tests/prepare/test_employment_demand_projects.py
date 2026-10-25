import datetime as dt
from typing import Any

import pytest

from smerisk.contracts import Consultant, EmploymentPeriod, FixedPriceProject, Sale, Timesheet
from smerisk.core.distributions import Empirical, Fixed, Normal
from smerisk.core.warnings import WarningCode
from smerisk.prepare.demand import arrival_rates, daily_demand, demand_distribution
from smerisk.prepare.employment import DAYS_PER_MONTH, merge_contracts, survival_subjects
from smerisk.prepare.projects import actual_cost, team_daily_cost

AS_OF = dt.date(2026, 10, 20)

# --- employment -----------------------------------------------------------------------------------------------


def period(
    employee: str, start: str, end: str | None = None, reason: str | None = None, **groups: str
) -> EmploymentPeriod:
    return EmploymentPeriod.model_validate(
        {"employee_ref": employee, "start": start, "end": end, "end_reason": reason, "groups": groups or None}
    )


def test_successive_contracts_are_merged() -> None:
    contracts = [
        period("e1", "2025-01-01", "2025-06-30", "END_OF_FIXED_TERM", contract_type="CDD"),
        period("e1", "2025-07-01", "2026-03-31", "RESIGNATION", contract_type="CDI"),  # the next day
        period("e2", "2025-01-01", "2025-03-31", "END_OF_FIXED_TERM"),
        period("e2", "2025-04-03", None),  # two days later: a new period
        period("e3", "2024-01-01", None),
        period("e3", "2025-01-01", "2025-02-01", "OTHER"),  # within a period still open
        period("e4", "2024-01-01", "2024-12-31", "OTHER"),
        period("e4", "2024-06-01", "2024-09-30", "DISMISSAL"),  # within the first contract
    ]
    merged = merge_contracts(contracts)
    e1 = [p for p in merged if p.employee_ref == "e1"]
    assert len(e1) == 1 and (e1[0].start, e1[0].end, e1[0].end_reason) == (
        dt.date(2025, 1, 1),
        dt.date(2026, 3, 31),
        "RESIGNATION",
    )
    assert e1[0].groups is not None and e1[0].groups.contract_type == "CDI"
    assert len([p for p in merged if p.employee_ref == "e2"]) == 2
    (e3,) = [p for p in merged if p.employee_ref == "e3"]
    assert e3.end is None and e3.end_reason is None
    (e4,) = [p for p in merged if p.employee_ref == "e4"]
    assert (e4.end, e4.end_reason) == (dt.date(2024, 12, 31), "OTHER")


def test_survival_subjects() -> None:
    periods = [
        period("e1", "2025-01-01", "2026-01-01", "RESIGNATION", site_ref="s1"),
        period("e2", "2025-01-01", "2025-07-01", "END_OF_FIXED_TERM"),
        period("e3", "2026-01-01"),
        period("e4", "2026-01-01", "2026-12-31", "DISMISSAL"),  # ends after the analysis date
        period("e5", "2026-11-01"),  # starts after it
        period("e6", "2025-06-01", "2025-09-01"),  # no reason: OTHER
    ]
    estimate = survival_subjects(periods, AS_OF, group_by="site_ref")
    subjects = estimate.value
    assert [(round(s.duration * DAYS_PER_MONTH), s.event) for s in subjects] == [
        (365, True),
        (181, False),
        (292, False),
        (292, False),
        (92, True),
    ]
    assert [s.group for s in subjects] == ["s1", "unknown", "unknown", "unknown", "unknown"]
    assert (estimate.n_points, estimate.first, estimate.last) == (5, "2025-01-01", "2026-10-20")
    assert survival_subjects(periods, AS_OF, exclude_reasons=()).value[1].event
    assert survival_subjects(periods, AS_OF).value[0].group is None


# --- demand ---------------------------------------------------------------------------------------------------


def sale(date: dt.date, quantity: float = 0, **fields: Any) -> Sale:
    return Sale.model_validate({"site_ref": "s1", "date": date, "quantity": quantity, "revenue_excl_tax": 0, **fields})


MONDAYS = [AS_OF - dt.timedelta(days=AS_OF.weekday() + 7 * w) for w in range(12)]  # the last 12 Mondays


def test_arrival_rates_by_slot() -> None:
    sales = [sale(d, hour=12, tickets=20 + i) for i, d in enumerate(MONDAYS[:8])]
    sales += [sale(MONDAYS[0], hour=13, tickets=16)]  # once: 0 on the seven other Mondays
    sales += [sale(MONDAYS[9], hour=12, tickets=500)]  # older than 8 weeks
    sales += [sale(MONDAYS[0], hour=None, tickets=40), sale(MONDAYS[0], hour=11)]  # unusable
    slots = arrival_rates(sales, AS_OF)
    assert [(s.weekday, s.hour, s.n_days) for s in slots] == [(0, 12, 8), (0, 13, 8)]
    assert slots[0].arrival_rate == pytest.approx(23.5) and not slots[0].irregular
    assert slots[1].arrival_rate == 2 and slots[1].variance == pytest.approx(32) and slots[1].irregular


def test_daily_demand_and_distribution() -> None:
    sales = [sale(d, quantity=30 + i, product_ref="quiche") for i, d in enumerate(MONDAYS[:10])]
    sales += [sale(MONDAYS[10], quantity=5, product_ref="tart")]  # quiche not sold that day: 0
    sales += [sale(MONDAYS[0] + dt.timedelta(days=1), quantity=99, product_ref="quiche")]  # a Tuesday
    demand = daily_demand(sales, "quiche", AS_OF, weekday=0)
    assert len(demand) == 11 and demand[MONDAYS[10]] == 0
    result = demand_distribution(demand)
    assert result is not None
    estimate, warnings = result
    assert isinstance(estimate.value, Empirical) and len(estimate.value.samples) == 22 and warnings == []
    assert sorted(set(estimate.value.samples)) == sorted(demand.values())
    assert len(daily_demand(sales, "quiche", AS_OF)) == 12  # every weekday


def test_few_days_give_a_normal_demand() -> None:
    result = demand_distribution({MONDAYS[0]: 10.0, MONDAYS[1]: 14.0}, name="quiche")
    assert result is not None
    estimate, warnings = result
    assert isinstance(estimate.value, Normal) and (estimate.value.mean, estimate.value.min) == (12, 0)
    assert estimate.value.sd == pytest.approx(8**0.5)
    assert [w.code for w in warnings] == [WarningCode.SMALL_SAMPLE]
    assert demand_distribution({}) is None
    single = demand_distribution({MONDAYS[0]: 10.0})
    assert single is not None and single[0].value == Normal(mean=10, sd=0, min=0)


# --- projects -------------------------------------------------------------------------------------------------

CONSULTANTS = [Consultant(consultant_ref="k1", daily_cost=400), Consultant(consultant_ref="k2", daily_cost=600)]


def project(team: list[Any], **fields: Any) -> FixedPriceProject:
    return FixedPriceProject.model_validate(
        {"project_ref": "p1", "price": 60_000, "cost_budget": 45_000, "deadline": "2026-12-18", "team": team, **fields}
    )


def test_team_daily_cost() -> None:
    estimate, missing = team_daily_cost(project(["k1", "k2", "k9"]), CONSULTANTS)
    assert missing == ["k9"] and estimate is not None
    assert estimate.value == Normal(mean=500, sd=100, min=0) and "equal weights" in estimate.method
    weighted, _ = team_daily_cost(
        project([{"consultant_ref": "k1", "planned_days": 30}, {"consultant_ref": "k2", "planned_days": 10}]),
        CONSULTANTS,
    )
    assert weighted is not None and weighted.value.mean == pytest.approx(450)  # type: ignore[union-attr]
    alone, _ = team_daily_cost(project(["k2"]), CONSULTANTS)
    assert alone is not None and alone.value == Fixed(value=600)
    assert team_daily_cost(project(["k9"]), CONSULTANTS) == (None, ["k9"])


def test_actual_cost() -> None:
    sheets = [
        Timesheet(consultant_ref="k1", period="2026-08", mission_ref="p1", billable_days=10),
        Timesheet(consultant_ref="k2", period="2026-09", mission_ref="p1", billable_days=5),
        Timesheet(consultant_ref="k2", period="2026-11", mission_ref="p1", billable_days=5),
        Timesheet(consultant_ref="k1", period="2026-09", mission_ref="other", billable_days=8),
    ]
    p = project(["k1", "k2"], actual_days=12)
    estimate = actual_cost(p, CONSULTANTS, sheets, until="2026-10")
    assert (
        estimate is not None
        and estimate.value == 10 * 400 + 5 * 600
        and (estimate.first, estimate.last)
        == (
            "2026-08",
            "2026-09",
        )
    )
    fallback = actual_cost(p, CONSULTANTS)
    assert fallback is not None and fallback.value == 12 * 500
    assert actual_cost(project(["k1"]), CONSULTANTS) is None
    assert actual_cost(project(["k9"], actual_days=3), CONSULTANTS) is None
    assert actual_cost(p, [], sheets) is None
