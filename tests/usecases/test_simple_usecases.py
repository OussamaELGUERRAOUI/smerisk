import datetime as dt
from typing import Any

import numpy as np
import pytest

from smerisk import InvalidInput, benford, brier, erlang_c, kupiec, newsvendor
from smerisk.core.warnings import WarningCode
from smerisk.usecases import (
    attrition,
    counter_staffing,
    forecast_reliability,
    fresh_orders,
    ledger_audit,
    rate_credibility,
)

AS_OF = dt.date(2026, 10, 20)
SETTINGS = {"as_of": AS_OF.isoformat()}
RNG = np.random.default_rng(11)

# --- ledger-audit ---------------------------------------------------------------------------------------------


def line(n: int, account: str, debit: float, date: str = "20260315") -> dict[str, Any]:
    return {
        "JournalCode": "AC",
        "EcritureNum": str(n),
        "EcritureDate": date,
        "CompteNum": account,
        "EcritureLib": "Supplier invoice",
        "Debit": f"{debit:.2f}".replace(".", ","),
        "Credit": "0,00",
    }


LEDGER = [line(i, "606100", float(a)) for i, a in enumerate(10 ** RNG.uniform(1.2, 4.5, 400))]
LEDGER += [line(500 + i, "411000", 1000.0 + i) for i in range(50)]  # customer accounts: not selected
LEDGER += [line(900, "606100", 777.0, date="20261105")]  # after as_of


def test_ledger_audit_selects_accounts_and_dates() -> None:
    out = ledger_audit(ledger_entries=LEDGER, settings=SETTINGS)
    direct = benford(amounts=[float(e["Debit"].replace(",", ".")) for e in LEDGER[:400]])
    assert out.result.n_used == direct.result.n_used == 400
    assert out.result.mad == pytest.approx(direct.result.mad)
    assert out.derived_parameters["amounts"].n_points == 400
    assert out.data_quality.usable and out.data_quality.coverage["ledger_entries"].last == "2026-03-15"
    assert out.assumptions["accounts"] == ["6", "2"] and out.assumptions["as_of"] == "2026-10-20"
    assert out.meta.method == "ledger-audit"


def test_ledger_audit_labels_and_overrides() -> None:
    uniform = [line(i, "606100", float(a)) for i, a in enumerate(RNG.uniform(100, 999, 300))]
    out = ledger_audit(ledger_entries=uniform, settings=SETTINGS)
    assert out.result.flagged_labels and all(label.startswith("AC-") for label in out.result.flagged_labels)
    forced = ledger_audit(ledger_entries=uniform, settings={**SETTINGS, "overrides": {"amounts": [150.0] * 120}})
    assert forced.derived_parameters["amounts"].source == "override" and forced.result.flagged_labels is None


def test_ledger_audit_small_or_empty() -> None:
    small = ledger_audit(ledger_entries=LEDGER[:50], settings=SETTINGS)
    assert not small.data_quality.usable and small.result.conformity is None
    with pytest.raises(InvalidInput):
        ledger_audit(ledger_entries=LEDGER[400:450], settings=SETTINGS)  # only customer accounts


# --- attrition ------------------------------------------------------------------------------------------------


def period(i: int, start: str, end: str | None = None, reason: str | None = None, site: str = "s1") -> dict[str, Any]:
    return {"employee_ref": f"e{i}", "start": start, "end": end, "end_reason": reason, "groups": {"site_ref": site}}


PERIODS = [period(i, "2024-01-01") for i in range(15)]
PERIODS += [period(15 + i, "2024-01-01", f"2025-0{i + 1}-15", "RESIGNATION", "s2") for i in range(8)]
PERIODS += [period(30, "2024-01-01", "2024-06-30", "END_OF_FIXED_TERM", "s2")]


def test_attrition_counts_events_and_censoring() -> None:
    out = attrition(employment_periods=PERIODS, settings={**SETTINGS, "group_by": "site_ref"})
    subjects = out.derived_parameters["subjects"].value
    assert len(subjects) == 24 and sum(s["event"] for s in subjects) == 8
    assert out.data_quality.usable
    curves = {c.group: (c.subjects, c.events) for c in out.result.curves}
    assert curves == {"ALL": (24, 8), "s1": (15, 0), "s2": (9, 8)}
    assert out.result.log_rank is not None
    assert out.assumptions["exclude_reasons"] == ["END_OF_FIXED_TERM", "RETIREMENT"]


def test_attrition_merges_contracts_and_rejects_both() -> None:
    contracts = [
        {"employee_ref": "e1", "start": "2025-01-01", "end": "2025-06-30", "end_reason": "END_OF_FIXED_TERM"},
        {"employee_ref": "e1", "start": "2025-07-01", "end": "2026-03-31", "end_reason": "RESIGNATION"},
    ]
    out = attrition(contracts=contracts, settings=SETTINGS)
    (subject,) = out.derived_parameters["subjects"].value
    assert subject["event"] and subject["duration"] == pytest.approx(15, abs=0.1)
    assert out.assumptions["contracts_merged"] and not out.data_quality.usable  # 1 employee < 20
    with pytest.raises(InvalidInput):
        attrition(contracts=contracts, employment_periods=contracts, settings=SETTINGS)


# --- rate-credibility -----------------------------------------------------------------------------------------

MONTHS = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)]


def absences(groups: dict[str, float], headcount: int = 10) -> list[dict[str, Any]]:
    rows = []
    for group, rate in groups.items():
        for month in MONTHS:
            theoretical = 21.0 * headcount
            absent = round(theoretical * rate * float(RNG.uniform(0.6, 1.4)), 1)
            rows.append(
                {
                    "period": month,
                    "group": group,
                    "absence_days": absent,
                    "theoretical_days": theoretical,
                    "headcount": headcount,
                }
            )
    return rows


def test_absenteeism_with_enough_groups_estimates_k() -> None:
    rows = absences({f"site{i}": 0.03 + 0.01 * i for i in range(6)})
    out = rate_credibility(absence_months=rows, settings=SETTINGS)
    assert out.result.parameters.k is not None and out.assumptions["mode"] == "estimate"
    assert out.assumptions["months"] == ["2024-10", "2026-09"]  # 24 complete months before October 2026
    estimates = {g.id: g.estimate for g in out.result.groups}
    assert estimates["site0"] < estimates["site5"]
    assert "reference_mean" not in out.derived_parameters


def test_few_groups_use_the_publisher_constant_and_the_overall_rate() -> None:
    rows = absences({"a": 0.04, "b": 0.06})
    out = rate_credibility(absence_months=rows, settings=SETTINGS)
    assert out.assumptions["mode"] == "reference"
    assert (
        out.derived_parameters["credibility_constant"].value == 4000
        and out.derived_parameters["credibility_constant"].source == "default"
    )
    used = [r for r in rows if "2024-10" <= r["period"] <= "2026-09"]
    overall = sum(r["absence_days"] for r in used) / sum(r["theoretical_days"] for r in used)
    assert out.derived_parameters["reference_mean"].value == pytest.approx(overall)
    group_a = out.result.groups[0]
    assert group_a.credibility == pytest.approx(group_a.exposure / (group_a.exposure + 4000))


def test_small_groups_are_merged_and_single_group_needs_a_reference() -> None:
    rows = absences({"a": 0.04, "b": 0.06}) + absences({"tiny": 0.2}, headcount=2)
    ids = [g.id for g in rate_credibility(absence_months=rows, settings=SETTINGS).result.groups]
    assert ids == ["a", "b", "others"]
    with pytest.raises(InvalidInput) as exc:
        rate_credibility(absence_months=absences({"a": 0.04}), settings=SETTINGS)
    assert exc.value.errors[0].path == ("settings", "reference_mean")
    given = rate_credibility(absence_months=absences({"a": 0.04}), settings={**SETTINGS, "reference_mean": 0.05})
    assert given.derived_parameters["reference_mean"].source == "override"


def test_turnover_rate_is_exits_over_employee_months() -> None:
    rows = [period(i, "2020-01-01", site="s1") for i in range(10)]
    rows += [period(10, "2020-01-01", "2026-03-15", "RESIGNATION", "s1")]  # 1 exit in March 2026
    rows += [period(20 + i, "2020-01-01", site="s2") for i in range(5)]
    out = rate_credibility(
        employment_periods=rows,
        settings={**SETTINGS, "indicator": "turnover", "group_by": "site_ref", "history_months": 12},
    )
    s1 = next(g for g in out.derived_parameters["groups"].value if g["id"] == "s1")
    march = s1["observations"][5]  # October 2025 is the first month
    assert march["value"] == pytest.approx(1 / (10 + 15 / 31)) and march["weight"] == pytest.approx(10 + 15 / 31)
    assert out.derived_parameters["credibility_constant"].value == 600
    assert out.assumptions["rate"] == "exits / employee-months"


# --- forecast-reliability -------------------------------------------------------------------------------------


def test_forecast_reliability_splits_probabilities_and_intervals() -> None:
    log = [
        {
            "forecast_ref": f"p{i}",
            "made_at": "2026-01-05",
            "target": "deal",
            "kind": "PROBABILITY",
            "p": p,
            "outcome": o,
        }
        for i, (p, o) in enumerate([(0.9, 1), (0.8, 1), (0.3, 0), (0.6, 0), (0.7, None)])
    ]
    log += [
        {
            "forecast_ref": f"i{i}",
            "made_at": "2026-02-01",
            "target": f"cash-{i}",
            "kind": "INTERVAL",
            "lower": 100,
            "level": 0.9,
            "outcome": 50 if i < 4 else 150,
        }
        for i in range(30)
    ]
    out = forecast_reliability(forecast_log=log, settings=SETTINGS)
    direct = brier(forecasts=[{"p": p, "outcome": o} for p, o in [(0.9, 1), (0.8, 1), (0.3, 0), (0.6, 0)]])
    assert out.result.probabilities is not None
    assert out.result.probabilities.brier_score == pytest.approx(direct.result.brier_score)
    (interval,) = out.result.intervals
    assert (interval.level, interval.exceedances, interval.observations) == (0.9, 4, 30)
    assert interval.test.p_value == pytest.approx(
        kupiec(exceedances=4, observations=30, expected_rate=0.1).result.p_value
    )
    assert not out.data_quality.usable  # 4 probabilities, 30 recommended


def test_forecast_reliability_needs_known_outcomes() -> None:
    log = [{"forecast_ref": "p", "made_at": "2026-01-05", "target": "deal", "kind": "PROBABILITY", "p": 0.5}]
    with pytest.raises(InvalidInput):
        forecast_reliability(forecast_log=log, settings=SETTINGS)


# --- fresh-orders ---------------------------------------------------------------------------------------------

MONDAYS = [AS_OF - dt.timedelta(days=AS_OF.weekday() + 7 * w) for w in range(12)]


def test_fresh_orders_matches_the_newsvendor() -> None:
    sales = [
        {
            "site_ref": "s1",
            "date": d.isoformat(),
            "product_ref": "quiche",
            "quantity": 30 + 2 * i,
            "revenue_excl_tax": 0,
        }
        for i, d in enumerate(MONDAYS)
    ]
    products = [{"product_ref": "quiche", "unit_price": 4.5, "unit_cost": 1.6, "salvage_value": 0.5}]
    out = fresh_orders(sales=sales, products=products, settings={**SETTINGS, "weekday": 0})
    (order,) = out.result.orders
    demand = out.derived_parameters["demand.quiche.monday"].value
    direct = newsvendor(demand=demand, unit_price=4.5, unit_cost=1.6, salvage_value=0.5)
    assert order.order.optimal_quantity == direct.result.optimal_quantity and order.weekday == 0
    forced = fresh_orders(
        sales=sales,
        products=products,
        settings={**SETTINGS, "weekday": 0, "overrides": {"demand.quiche.monday": {"type": "fixed", "value": 10}}},
    )
    assert forced.result.orders[0].order.optimal_quantity == 10


def test_fresh_orders_reports_days_without_sales() -> None:
    sales = [
        {"site_ref": "s1", "date": MONDAYS[0].isoformat(), "product_ref": "tart", "quantity": 5, "revenue_excl_tax": 0}
    ]
    products = [{"product_ref": "tart", "unit_price": 3, "unit_cost": 1}]
    out = fresh_orders(sales=sales, products=products, settings=SETTINGS)
    assert [o.weekday for o in out.result.orders] == [0]
    assert not out.data_quality.usable
    assert [w.code for w in out.warnings] == [WarningCode.SMALL_SAMPLE]


# --- counter-staffing -----------------------------------------------------------------------------------------


def test_counter_staffing_matches_erlang_c() -> None:
    sales = [
        {"site_ref": "s1", "date": d.isoformat(), "hour": 12, "tickets": 40, "quantity": 0, "revenue_excl_tax": 0}
        for d in MONDAYS[:8]
    ]
    sales += [
        {
            "site_ref": "s1",
            "date": MONDAYS[0].isoformat(),
            "hour": 15,
            "tickets": 0,
            "quantity": 0,
            "revenue_excl_tax": 0,
        }
    ]
    out = counter_staffing(sales=sales, settings={**SETTINGS, "service_time_minutes": 4})
    noon, afternoon = out.result.slots
    direct = erlang_c(arrival_rate=40, service_time_minutes=4, target_wait_seconds=180, target_service_level=0.8)
    assert noon.staffing is not None and noon.staffing.recommended_agents == direct.result.recommended_agents
    assert afternoon.staffing is None and afternoon.arrival_rate == 0
    forced = counter_staffing(
        sales=sales, settings={**SETTINGS, "service_time_minutes": 4, "overrides": {"arrival_rate.s1.0.12": 80.0}}
    )
    assert forced.result.slots[0].staffing is not None
    assert forced.result.slots[0].staffing.recommended_agents > direct.result.recommended_agents  # type: ignore[operator]


def test_counter_staffing_requires_service_time() -> None:
    with pytest.raises(InvalidInput):
        counter_staffing(sales=[], settings=SETTINGS)
