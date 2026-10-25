import datetime as dt
from typing import Any

import numpy as np
import pytest

from smerisk import InvalidInput, fixed_price_completion
from smerisk.core.warnings import WarningCode
from smerisk.usecases import cash_at_risk, fixed_price_risk, payroll_at_risk, receivables_risk, utilization_forecast

AS_OF = dt.date(2026, 10, 20)
SETTINGS: dict[str, Any] = {"as_of": AS_OF.isoformat(), "seed": 7, "n_simulations": 2_000}


def months(first: str, n: int) -> list[str]:
    y, m = int(first[:4]), int(first[5:])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# --- receivables-risk -----------------------------------------------------------------------------------------


def invoices(n: int = 120, seed: int = 3) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        issue = dt.date(2024, 11, 1) + dt.timedelta(days=int(i * 5.8))
        due = issue + dt.timedelta(days=30)
        late = int(rng.choice([0, 0, 0, 10, 25, 45, 80]))
        paid = due + dt.timedelta(days=late)
        row: dict[str, Any] = {
            "invoice_ref": f"F{i}",
            "customer_ref": f"c{i % 6}",
            "issue_date": issue.isoformat(),
            "due_date": due.isoformat(),
            "amount": float(rng.integers(500, 5_000)),
        }
        if i % 23 == 5 and paid < AS_OF:
            row["written_off_date"] = (due + dt.timedelta(days=120)).isoformat()
        elif paid < AS_OF:
            row["payments"] = [{"date": paid.isoformat(), "amount": row["amount"]}]
        rows.append(row)
    return rows


def test_receivables_risk_values_open_invoices() -> None:
    out = receivables_risk(invoices=invoices(), settings=SETTINGS)
    transitions = out.derived_parameters["transitions"].value
    assert transitions and {t["from"] for t in transitions} <= {"CURRENT", "LATE_30", "LATE_60", "LATE_90"}
    p_default = out.result.ageing.absorption.p_default_from
    assert all(0 <= p <= 1 for p in p_default.values()) and p_default["LATE_90"] > p_default["CURRENT"]
    exposure = out.derived_parameters["current_exposure"].value
    assert out.result.expected_loss.expected_loss == pytest.approx(
        sum(exposure[s] * p_default[s] for s in exposure), abs=0.05
    )
    halved = receivables_risk(invoices=invoices(), settings={**SETTINGS, "overrides": {"lgd": 0.5}})
    assert halved.result.expected_loss.expected_loss == pytest.approx(
        out.result.expected_loss.expected_loss / 2, abs=0.05
    )
    assert halved.derived_parameters["lgd"].source == "override"


def test_receivables_risk_from_a_fec_and_errors() -> None:
    fec = [
        {
            "JournalCode": "VT",
            "EcritureNum": str(i),
            "EcritureDate": d,
            "CompteNum": "411000",
            "CompAuxNum": f"C{i % 3}",
            "PieceRef": f"F{i}",
            "Debit": "1000,00",
            "Credit": "0,00",
        }
        for i, d in enumerate(["20260105", "20260210", "20260315", "20260420", "20260525", "20260630", "20260905"])
    ]
    fec += [
        {
            "JournalCode": "VT",
            "EcritureNum": str(i),
            "EcritureDate": "20260105",
            "CompteNum": "706000",
            "Debit": "0,00",
            "Credit": "1000,00",
        }
        for i in range(1)
    ]
    out = receivables_risk(ledger_entries=fec, settings=SETTINGS)
    assert out.result.expected_loss.expected_loss > 0
    assert not out.data_quality.usable  # less than 24 months of history
    with pytest.raises(InvalidInput):
        receivables_risk(invoices=invoices(), ledger_entries=fec, settings=SETTINGS)


# --- fixed-price-risk -----------------------------------------------------------------------------------------

PROJECT = {
    "project_ref": "p1",
    "price": 60_000,
    "cost_budget": 45_000,
    "deadline": "2026-12-18",
    "remaining_tasks": [
        {"task_ref": "t1", "optimistic": 10, "most_likely": 15, "pessimistic": 25},
        {"task_ref": "t2", "optimistic": 5, "most_likely": 8, "pessimistic": 14},
        {"task_ref": "t3", "optimistic": 2, "most_likely": 2, "pessimistic": 2},
    ],
    "risks": [{"risk_ref": "r1", "probability": 0.3, "impact_days_most_likely": 3, "impact_days_max": 8}],
    "team": ["k1", "k2"],
}
CONSULTANTS = [{"consultant_ref": "k1", "daily_cost": 450}, {"consultant_ref": "k2", "daily_cost": 550}]
TIMESHEETS = [
    {"consultant_ref": "k1", "period": "2026-09", "mission_ref": "p1", "billable_days": 12},
    {"consultant_ref": "k2", "period": "2026-10", "mission_ref": "p1", "billable_days": 8},
]


def test_fixed_price_risk_matches_the_formula() -> None:
    out = fixed_price_risk(
        fixed_price_projects=[PROJECT], consultants=CONSULTANTS, timesheets=TIMESHEETS, settings=SETTINGS
    )
    (project,) = out.result.projects
    base = out.derived_parameters
    assert base["projects.p1.actual_cost"].value == 12 * 450 + 8 * 550
    assert [t["id"] for t in base["projects.p1.remaining_tasks"].value] == ["t1", "t2"]
    assert [r["id"] for r in base["projects.p1.risks"].value] == ["t3", "r1"]  # t3 has no spread: a certain cost
    assert project.deadline_days == 43  # working days from 21 October to 18 December 2026
    direct = fixed_price_completion(
        contract_price=60_000,
        actual_cost=base["projects.p1.actual_cost"].value,
        cost_budget=45_000,
        remaining_tasks=base["projects.p1.remaining_tasks"].value,
        risks=base["projects.p1.risks"].value,
        daily_cost=base["projects.p1.daily_cost"].value,
        deadline_days=43,
        team_capacity_days_per_period=2,
        n_simulations=2_000,
        seed=7,
        quantiles=out.assumptions["quantiles"],
    )
    assert project.completion == direct.result
    assert out.assumptions["seed"] == 7 and out.data_quality.usable


def test_fixed_price_risk_overrides_and_missing_data() -> None:
    forced = fixed_price_risk(
        fixed_price_projects=[PROJECT],
        consultants=CONSULTANTS,
        settings={**SETTINGS, "overrides": {"projects.p1.daily_cost": {"type": "fixed", "value": 1000}}},
    )
    assert forced.derived_parameters["projects.p1.actual_cost"].source == "default"
    assert not forced.data_quality.usable  # no timesheet and no actual_days
    late = fixed_price_risk(
        fixed_price_projects=[{**PROJECT, "deadline": "2026-10-01"}], consultants=CONSULTANTS, settings=SETTINGS
    )
    assert late.result.projects[0].deadline_days is None
    with pytest.raises(InvalidInput):
        fixed_price_risk(fixed_price_projects=[{**PROJECT, "team": ["k9"]}], consultants=CONSULTANTS, settings=SETTINGS)


# --- utilization-forecast -------------------------------------------------------------------------------------


def mission(ref: str, consultant: str, customer: str, start: str, end: str, **fields: Any) -> dict[str, Any]:
    return {
        "mission_ref": ref,
        "consultant_ref": consultant,
        "customer_ref": customer,
        "start": start,
        "planned_end": end,
        "billing_type": "TIME",
        **fields,
    }


MISSIONS = [
    mission("m1", "k1", "c1", "2025-01-06", "2025-06-30"),
    mission("m2", "k1", "c1", "2025-07-01", "2025-12-31", renewal_of="m1"),
    mission("m3", "k1", "c1", "2026-01-05", "2026-11-30", renewal_of="m2"),
    mission("m4", "k2", "c2", "2025-03-03", "2025-09-30"),
    mission("m5", "k2", "c2", "2026-02-02", "2027-01-29"),
]
DEALS = [
    {
        "deal_ref": "d1",
        "customer_ref": "c3",
        "stage": "proposal",
        "amount": 80_000,
        "outcome": "OPEN",
        "expected_start": "2027-01-04",
        "consultants_needed": 1,
        "expected_duration_months": 4,
        "stages_history": [{"stage": "lead", "date": "2026-08-01"}, {"stage": "proposal", "date": "2026-09-15"}],
    },
    {
        "deal_ref": "d0",
        "customer_ref": "c4",
        "stage": "proposal",
        "amount": 50_000,
        "outcome": "WON",
        "stages_history": [{"stage": "lead", "date": "2026-03-01"}, {"stage": "proposal", "date": "2026-04-01"}],
    },
]


def test_utilization_forecast_prepares_renewals_and_deals() -> None:
    consultants = [{"consultant_ref": k, "daily_cost": 400} for k in ("k1", "k2", "k3")]
    sheets = [
        {"consultant_ref": "k1", "period": p, "mission_ref": "m3", "billable_days": 19} for p in months("2026-04", 6)
    ]
    out = utilization_forecast(
        consultants=consultants, missions=MISSIONS, timesheets=sheets, deals=DEALS, settings=SETTINGS
    )
    assert out.assumptions["periods"] == months("2026-11", 6)
    derived = out.derived_parameters
    assert derived["consultants.k1.billable_days_per_period"].value == 19
    beta = derived["consultants.k1.renewal_probability_beta"].value
    assert set(beta) == {"alpha", "beta"} and beta["alpha"] > beta["beta"]  # c1 renews
    assert derived["deals.d1.win_probability"].value == pytest.approx((1 + 0.75 * 4) / (1 + 4))  # 1 won of 1, prior 75%
    assert derived["consultants.k3.available_days"].value["2026-11"] == 21
    again = utilization_forecast(
        consultants=consultants, missions=MISSIONS, timesheets=sheets, deals=DEALS, settings=SETTINGS
    )
    assert again.result == out.result
    forced = utilization_forecast(
        consultants=consultants,
        missions=MISSIONS,
        deals=DEALS,
        settings={**SETTINGS, "overrides": {"deals.d1.win_probability": 0.0}},
    )
    assert forced.derived_parameters["deals.d1.win_probability"].source == "override"


# --- payroll-at-risk ------------------------------------------------------------------------------------------


def payslips(n_months: int = 24, seed: int = 5) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    rows = []
    for period in months("2024-10", n_months):
        for e in range(8):
            overtime = float(rng.gamma(2, 60)) * (1.6 if period.endswith("-12") else 1)
            rows.append(
                {
                    "employee_ref": f"e{e}",
                    "period": period,
                    "gross": 2500,
                    "employer_contributions": 700,
                    "employer_cost": 3200,
                    "by_family": {
                        "base": 2200,
                        "overtime": round(overtime, 2),
                        "sick_pay": round(float(rng.exponential(40)), 2),
                    },
                }
            )
    return rows


BASELINE = [
    {"period": "2026-11", "fixed_cost": 26_000},
    {"period": "2026-12", "fixed_cost": 26_000, "planned_hires": [{"start_period": "2026-12", "monthly_cost": 3_000}]},
    {"period": "2027-01", "fixed_cost": 26_500},
]


def test_payroll_at_risk_simulates_variable_parts_and_hires() -> None:
    out = payroll_at_risk(payslips=payslips(), payroll_baseline=BASELINE, settings=SETTINGS)
    ids = [c.id for c in out.result.contributions]
    assert ids == ["fixed_cost", "overtime", "sick_pay", "hire.2026-12.1"]
    overtime = out.derived_parameters["components.overtime"].value
    assert set(overtime) == {"2026-11", "2026-12", "2027-01"}
    assert out.derived_parameters["components.night"].source == "default"
    assert "correlation.overtime~sick_pay" in out.derived_parameters
    assert out.result.horizon.cfar > 0 and out.assumptions["orientation"] == "lower_is_better"
    dec = out.result.by_period[1].expected_flow
    assert dec == pytest.approx(26_000 + 0.8 * 3_000 + 8 * 120 * 1.6 + 8 * 40, rel=0.08)
    assert out.data_quality.usable and WarningCode.NO_SEASONALITY not in [w.code for w in out.warnings]


def test_payroll_hire_override_and_specification_example() -> None:
    sure = payroll_at_risk(
        payslips=payslips(),
        payroll_baseline=BASELINE,
        settings={**SETTINGS, "overrides": {"components.planned_hires.probability": 1.0}},
    )
    hire_flow = sure.result.by_period[2].flow_quantiles
    assert hire_flow["0.05"] > 26_500 + 3_000 - 1  # the hire is certain in January
    example = payroll_at_risk(
        payslips=[
            {
                "employee_ref": "e1",
                "period": "2026-01",
                "gross": 2150.0,
                "employer_contributions": 610.4,
                "employer_cost": 2760.4,
                "by_family": {"base": 1900.0, "overtime": 180.0, "night": 70.0},
            }
        ],
        payroll_baseline=[
            {
                "period": "2027-01",
                "fixed_cost": 48200.0,
                "planned_hires": [{"start_period": "2027-03", "monthly_cost": 2900.0}],
            }
        ],
        settings={**SETTINGS, "quantiles": [0.1, 0.5, 0.9]},
    )
    codes = {w.code for w in example.warnings}
    assert {WarningCode.SMALL_SAMPLE, WarningCode.NO_SEASONALITY, WarningCode.DEFAULT_CORRELATION} <= codes
    assert not example.data_quality.usable
    assert example.result.by_period[0].expected_flow == pytest.approx(48200 + 180 + 70)


# --- cash-at-risk ---------------------------------------------------------------------------------------------


def open_invoice(
    i: int, due: str, amount: float, sector: str | None = None, customer: str | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "invoice_ref": f"O{i}",
        "customer_ref": customer or f"c{i}",
        "issue_date": "2026-09-01",
        "due_date": due,
        "amount": amount,
    }
    if sector:
        row["customer_sector"] = sector
    return row


def test_cash_at_risk_combines_receipts_and_known_flows() -> None:
    history = invoices()
    open_rows = [
        open_invoice(1, "2026-11-02", 10_000, "retail", "shop1"),
        open_invoice(2, "2026-11-04", 8_000, "retail", "shop2"),
        open_invoice(3, "2026-09-15", 5_000, None, "slow"),  # already 35 days late
    ]
    flows = [{"date": "2026-10-30", "amount": 18_000, "direction": "OUT", "category": "payroll"}]
    out = cash_at_risk(
        bank_balances=[{"date": "2026-10-19", "amount": 25_000}, {"date": "2026-10-01", "amount": 1}],
        invoices=history + open_rows,
        scheduled_flows=flows,
        settings=SETTINGS,
    )
    periods = [p.period for p in out.result.by_period]
    assert periods[0] == "2026-W43" and len(periods) == 13  # from the remaining days of the current week
    assert out.assumptions["opening_balance"] == 25_000
    ids = [c.id for c in out.result.contributions]
    assert "scheduled_flows" in ids
    receipts = {
        i.split(".")[1]: i.split(".")[2]
        for i in ids
        if i.startswith("receipts.") and i.split(".")[1] in ("shop1", "shop2", "slow")
    }
    assert receipts["shop1"] >= "2026-W45" and receipts["slow"] >= "2026-W43"  # never before the due date or today
    assert out.assumptions["correlated_receipts"]["retail"] >= 2  # receipts of the retail customers are correlated
    flows_by_week = {p.period: p.expected_flow for p in out.result.by_period}
    assert flows_by_week["2026-W44"] < flows_by_week["2026-W43"]  # the payroll goes out on 30 October
    assert 0 <= out.result.by_period[-1].p_below_threshold <= 1  # type: ignore[operator]


def test_cash_at_risk_reads_recurring_payments_from_the_fec() -> None:
    entries = []
    n = 0
    for week in range(63):  # up to the week before as_of
        day = (dt.date(2025, 8, 4) + dt.timedelta(weeks=week)).strftime("%Y%m%d")
        n += 1
        entries += [
            {
                "JournalCode": "BQ",
                "EcritureNum": str(n),
                "EcritureDate": day,
                "CompteNum": "401000",
                "Debit": "2000,00",
                "Credit": "0,00",
            },
            {
                "JournalCode": "BQ",
                "EcritureNum": str(n),
                "EcritureDate": day,
                "CompteNum": "512000",
                "Debit": "0,00",
                "Credit": "2000,00",
            },
        ]
        if week % 4 == 0:
            n += 1
            entries += [
                {
                    "JournalCode": "BQ",
                    "EcritureNum": str(n),
                    "EcritureDate": day,
                    "CompteNum": "421000",
                    "Debit": "9000,00",
                    "Credit": "0,00",
                },
                {
                    "JournalCode": "BQ",
                    "EcritureNum": str(n),
                    "EcritureDate": day,
                    "CompteNum": "512000",
                    "Debit": "0,00",
                    "Credit": "9000,00",
                },
            ]
    entries += [
        {
            "JournalCode": "VT",
            "EcritureNum": "S1",
            "EcritureDate": "20261001",
            "CompteNum": "411000",
            "CompAuxNum": "C1",
            "PieceRef": "F1",
            "Debit": "12000,00",
            "Credit": "0,00",
        },
    ]
    out = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 50_000}], ledger_entries=entries, settings=SETTINGS
    )
    suppliers = out.derived_parameters["recurring.suppliers"].value
    assert suppliers.type == "fixed" and suppliers.value == 2000  # type: ignore[union-attr]
    payroll = out.derived_parameters["recurring.payroll"]
    assert payroll.n_points == 52 and sorted(set(payroll.value.samples)) == [0.0, 9000.0]  # type: ignore[union-attr]
    replaced = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 50_000}],
        ledger_entries=entries,
        scheduled_flows=[{"date": "2026-10-30", "amount": 9_000, "direction": "OUT", "category": "payroll"}],
        settings=SETTINGS,
    )
    assert "recurring.payroll" not in replaced.derived_parameters
    assert replaced.assumptions["replaced_by_scheduled_flows"] == ["payroll"]


def test_cash_at_risk_pools_small_customers_beyond_the_component_limit() -> None:
    rows = [open_invoice(i, "2026-11-10", 100 + i) for i in range(260)]
    out = cash_at_risk(bank_balances=[{"date": "2026-10-20", "amount": 0}], invoices=rows, settings=SETTINGS)
    ids = [c.id for c in out.result.contributions]
    assert len(ids) <= 200 and any(i.startswith("receipts.other_customers") for i in ids)
    assert out.derived_parameters["receipts.on_time_probability"].source == "default"


def test_cash_at_risk_errors() -> None:
    with pytest.raises(InvalidInput):
        cash_at_risk(bank_balances=[{"date": "2026-11-01", "amount": 0}], invoices=invoices(), settings=SETTINGS)


def test_cash_at_risk_fixed_delay_other_receipts_and_transfers() -> None:
    def pair(n: str, day: str, debit_account: str, credit_account: str, amount: str) -> list[dict[str, Any]]:
        common = {"JournalCode": "BQ", "EcritureNum": n, "EcritureDate": day}
        return [
            {**common, "CompteNum": debit_account, "Debit": amount, "Credit": "0,00"},
            {**common, "CompteNum": credit_account, "Debit": "0,00", "Credit": amount},
        ]

    entries = pair("1", "20260601", "512000", "740000", "5000,00")  # a subsidy received
    entries += pair("2", "20260602", "580000", "512000", "3000,00")  # a transfer between accounts: ignored
    entries += pair("3", "20260603", "512000", "411000", "700,00")  # a customer payment: from the invoices
    entries += [
        {
            "JournalCode": "VT",
            "EcritureNum": "4",
            "EcritureDate": "20260815",
            "CompteNum": "411000",
            "CompAuxNum": "C1",
            "PieceRef": "F4",
            "Debit": "4000,00",
            "Credit": "0,00",
        },
    ]
    out = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 1_000}],
        ledger_entries=entries,
        settings={**SETTINGS, "overrides": {"receipts.late_delay_days": {"type": "fixed", "value": 20}}},
    )
    assert set(p for p in out.derived_parameters if p.startswith("recurring.")) == {"recurring.other_receipts"}
    ids = [c.id for c in out.result.contributions]
    assert "other_receipts" in ids and not any(
        i.startswith("receipts.C1") for i in ids
    )  # due 14 September: 36 days late > 20, lost

    lone = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 1_000}],
        invoices=[open_invoice(1, "2026-11-02", 1_000, "retail", "shop1")],
        settings=SETTINGS,
    )
    assert lone.assumptions["correlated_receipts"] == {}
    fixed = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 1_000}],
        invoices=[open_invoice(1, "2026-11-02", 1_000, None, "shop1")],
        settings={**SETTINGS, "overrides": {"receipts.late_delay_days": {"type": "fixed", "value": 20}}},
    )
    assert fixed.result.by_period[-1].expected_cumulative == pytest.approx(2_000, abs=1)  # paid by the end


def test_cash_at_risk_receives_an_invoice_once_at_most() -> None:
    history = invoices()  # about half the invoices are paid late, some far beyond 30 days
    out = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 0}],
        invoices=[*history, open_invoice(1, "2026-11-02", 1_000, None, "solo")],
        settings={**SETTINGS, "quantiles": [0.01, 0.25, 0.5, 0.75, 0.99]},
    )
    parts = [c.id for c in out.result.contributions if c.id.startswith("receipts.solo")]
    assert len(parts) == 2 and parts[1].endswith(".late")  # on time, or late within the horizon
    solo_only = cash_at_risk(
        bank_balances=[{"date": "2026-10-20", "amount": 0}],
        invoices=[open_invoice(1, "2026-11-02", 1_000, None, "solo")],
        settings={**SETTINGS, "quantiles": [0.01, 0.5, 0.99]},
    )
    for period in solo_only.result.by_period:
        assert set(period.cumulative_quantiles.values()) <= {0.0, 1_000.0}  # never twice
