import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from smerisk import InputTooLarge
from smerisk.contracts import (
    MAX_IGNORED_LISTED,
    AbsenceMonth,
    EmploymentPeriod,
    FixedPriceProject,
    ForecastLogEntry,
    Invoice,
    LedgerEntry,
    Mission,
    Payslip,
    ScheduledFlow,
    Timesheet,
    _base,
    parse_records,
)

PAYSLIP = {
    "employee_ref": "e1",
    "period": "2026-01",
    "gross": 2150.0,
    "employer_contributions": 610.4,
    "employer_cost": 2760.4,
    "by_family": {"base": 1900.0, "overtime": 180.0, "night": 70.0},
}

# --- parse_records --------------------------------------------------------------------------------------------


def test_invalid_records_are_set_aside_with_their_reason() -> None:
    rows: list[Any] = [PAYSLIP, {**PAYSLIP, "period": "2026-13"}, {**PAYSLIP, "gross": None}, "not a record"]
    parsed = parse_records(Payslip, rows, name="payslips")
    assert len(parsed.records) == 1 and parsed.n_ignored == 3
    assert [(i.index, i.field) for i in parsed.ignored] == [(1, "period"), (2, "gross"), (3, None)]
    assert "period" in parsed.ignored[0].reason


def test_validated_records_pass_through() -> None:
    slip = Payslip.model_validate(PAYSLIP)
    assert parse_records(Payslip, [slip], name="payslips").records == [slip]


def test_only_the_first_ignored_records_are_listed() -> None:
    parsed = parse_records(Payslip, [{}] * (MAX_IGNORED_LISTED + 5), name="payslips")
    assert parsed.n_ignored == MAX_IGNORED_LISTED + 5 and len(parsed.ignored) == MAX_IGNORED_LISTED


def test_record_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_base, "MAX_RECORDS", 2)
    with pytest.raises(InputTooLarge) as exc:
        parse_records(Payslip, [PAYSLIP] * 3, name="payslips")
    assert exc.value.field == "payslips"


def test_unknown_fields_and_nan_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Payslip.model_validate({**PAYSLIP, "name": "Jane Doe"})
    with pytest.raises(ValidationError):
        Payslip.model_validate({**PAYSLIP, "gross": float("nan")})
    with pytest.raises(ValidationError):
        Payslip.model_validate({**PAYSLIP, "by_family": {"tips": 10}})


def test_refs_are_stripped_and_never_empty() -> None:
    assert Payslip.model_validate({**PAYSLIP, "employee_ref": " e1 "}).employee_ref == "e1"
    with pytest.raises(ValidationError):
        Payslip.model_validate({**PAYSLIP, "employee_ref": "  "})


# --- payroll --------------------------------------------------------------------------------------------------


def test_absence_days_within_theoretical_days() -> None:
    AbsenceMonth(period="2026-01", absence_days=12, theoretical_days=210, headcount=10)
    with pytest.raises(ValidationError):
        AbsenceMonth(period="2026-01", absence_days=220, theoretical_days=210, headcount=10)


@pytest.mark.parametrize(
    "fields",
    [
        {"start": "2026-03-01", "end": "2026-02-01", "end_reason": "RESIGNATION"},
        {"start": "2026-03-01", "end_reason": "RESIGNATION"},
    ],
)
def test_employment_period_dates(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EmploymentPeriod.model_validate({"employee_ref": "e1", **fields})


# --- accounting -----------------------------------------------------------------------------------------------

FEC_LINE = {
    "JournalCode": "VT",
    "EcritureNum": "VT-118",
    "EcritureDate": "20260131",
    "CompteNum": "411000",
    "CompAuxNum": "C042",
    "EcritureLib": "Invoice 118",
    "PieceRef": "F118",
    "Debit": "1 234,56",
    "Credit": "0,00",
    "EcritureLet": "",
    "DateLet": "",
}


def test_ledger_entry_reads_fec_formats_and_drops_labels() -> None:
    e = LedgerEntry.model_validate(FEC_LINE)
    assert e.entry_date == dt.date(2026, 1, 31)
    assert (e.debit, e.credit, e.balance) == (1234.56, 0.0, 1234.56)
    assert e.lettering is None and e.lettering_date is None
    assert "EcritureLib" not in e.model_dump() and e.model_dump()["CompteNum"] == "411000"


def test_ledger_entry_accepts_python_names() -> None:
    e = LedgerEntry(
        journal_code="VT", entry_number="1", entry_date=dt.date(2026, 1, 31), account="706", debit=0, credit=100
    )
    assert e.balance == -100


@pytest.mark.parametrize(("direction", "debit", "credit"), [("D", 50.0, 0.0), ("C", 0.0, 50.0), ("-1", 0.0, 50.0)])
def test_amount_and_direction_variant(direction: str, debit: float, credit: float) -> None:
    line = {k: v for k, v in FEC_LINE.items() if k not in ("Debit", "Credit")}
    e = LedgerEntry.model_validate({**line, "Montant": "50,00", "Sens": direction})
    assert (e.debit, e.credit) == (debit, credit)


def test_unknown_direction_is_rejected() -> None:
    line = {k: v for k, v in FEC_LINE.items() if k not in ("Debit", "Credit")}
    with pytest.raises(ValidationError):
        LedgerEntry.model_validate({**line, "Montant": "50,00", "Sens": "X"})


def test_invoice_dates() -> None:
    base = {"invoice_ref": "F1", "customer_ref": "C1", "issue_date": "2026-01-10", "amount": 1200}
    invoice = Invoice.model_validate(
        {**base, "due_date": "2026-02-09", "payments": [{"date": "2026-02-20", "amount": 1200}]}
    )
    assert invoice.payments[0].date == dt.date(2026, 2, 20)
    with pytest.raises(ValidationError):
        Invoice.model_validate({**base, "due_date": "2026-01-01"})
    with pytest.raises(ValidationError):
        Invoice.model_validate({**base, "due_date": "2026-02-09", "amount": -50})
    with pytest.raises(ValidationError):
        Invoice.model_validate({**base, "due_date": "2026-02-09", "written_off_date": "2025-12-31"})
    entry = LedgerEntry.model_validate({**FEC_LINE, "PieceDate": "2026-01-30"})  # ISO dates are accepted too
    assert entry.document_date == dt.date(2026, 1, 30)


def test_scheduled_flow_sign() -> None:
    flow = ScheduledFlow(date=dt.date(2026, 11, 5), amount=18_000, direction="OUT", category="payroll")
    assert flow.signed_amount == -18_000


# --- consulting -----------------------------------------------------------------------------------------------


def test_timesheet_rules() -> None:
    Timesheet(consultant_ref="k1", period="2026-01", non_billable_days=3)
    with pytest.raises(ValidationError):
        Timesheet(consultant_ref="k1", period="2026-01", billable_days=5)  # billable time needs a mission
    with pytest.raises(ValidationError):
        Timesheet(consultant_ref="k1", period="2026-01", mission_ref="m1", billable_days=25, leave_days=10)


def test_mission_rules() -> None:
    base = {"mission_ref": "m1", "customer_ref": "c1", "consultant_ref": "k1", "billing_type": "TIME"}
    Mission.model_validate({**base, "start": "2026-01-01", "planned_end": "2026-06-30"})
    with pytest.raises(ValidationError):
        Mission.model_validate({**base, "start": "2026-01-01", "planned_end": "2025-12-31"})
    with pytest.raises(ValidationError):
        Mission.model_validate({**base, "start": "2026-01-01", "planned_end": "2026-06-30", "renewal_of": "m1"})


def test_fixed_price_project() -> None:
    project = FixedPriceProject.model_validate(
        {
            "project_ref": "p1",
            "price": 60_000,
            "cost_budget": 45_000,
            "deadline": "2026-12-18",
            "remaining_tasks": [{"task_ref": "t1", "optimistic": 4, "most_likely": 6, "pessimistic": 10}],
            "risks": [{"risk_ref": "r1", "probability": 0.3, "impact_days_most_likely": 3, "impact_days_max": 8}],
            "team": ["k1", {"consultant_ref": "k2", "planned_days": 20}],
        }
    )
    assert [m.consultant_ref for m in project.team] == ["k1", "k2"] and project.team[0].planned_days is None
    with pytest.raises(ValidationError):
        FixedPriceProject.model_validate({**project.model_dump(), "team": ["k1", "k1"]})
    with pytest.raises(ValidationError):
        FixedPriceProject.model_validate(
            {
                **project.model_dump(),
                "remaining_tasks": [{"task_ref": "t1", "optimistic": 6, "most_likely": 4, "pessimistic": 10}],
            }
        )


# --- forecasts ------------------------------------------------------------------------------------------------


def test_probability_forecast() -> None:
    f = ForecastLogEntry(forecast_ref="f1", made_at="2026-01-05", target="deal-7", kind="PROBABILITY", p=0.7, outcome=1)
    assert f.made_at == dt.datetime(2026, 1, 5) and f.exceeded is None
    for bad in ({"p": None}, {"outcome": 0.5}, {"level": 0.9}):
        with pytest.raises(ValidationError):
            ForecastLogEntry.model_validate({**f.model_dump(), **bad})


def test_interval_forecast_exceedance() -> None:
    base = {"forecast_ref": "f2", "made_at": "2026-01-05T09:30:00", "target": "cash-2026-02", "kind": "INTERVAL"}
    floor = ForecastLogEntry.model_validate({**base, "lower": 10_000, "level": 0.95, "outcome": 8_000})
    assert floor.exceeded is True
    inside = ForecastLogEntry.model_validate(
        {**base, "lower": 10_000, "upper": 30_000, "level": 0.9, "outcome": 12_000}
    )
    assert inside.exceeded is False
    assert ForecastLogEntry.model_validate({**base, "upper": 5, "level": 0.9}).exceeded is None
    for bad in (
        {"lower": 10},
        {"level": 0.9},
        {"lower": 3, "upper": 2, "level": 0.9},
        {"p": 0.5, "lower": 1, "level": 0.9},
    ):
        with pytest.raises(ValidationError):
            ForecastLogEntry.model_validate({**base, **bad})


def test_valid_records_of_each_kind() -> None:
    period = EmploymentPeriod.model_validate(
        {"employee_ref": "e1", "start": "2024-03-01", "end": "2026-02-28", "end_reason": "RESIGNATION"}
    )
    assert period.end == dt.date(2026, 2, 28)
    flow = ScheduledFlow(date=dt.date(2026, 11, 5), amount=4_000, direction="IN", category="subsidy")
    assert flow.signed_amount == 4_000
    entry = LedgerEntry.model_validate({**FEC_LINE, "EcritureDate": dt.date(2026, 1, 31), "Debit": 10})
    assert (entry.entry_date, entry.debit) == (dt.date(2026, 1, 31), 10.0)
    with pytest.raises(ValidationError):
        FixedPriceProject.model_validate(
            {"project_ref": "p1", "price": 1, "cost_budget": 1, "deadline": "2026-12-18", "team": "k1"}
        )


def test_inconsistent_mission_end_and_risk_impact() -> None:
    with pytest.raises(ValidationError):
        Mission.model_validate(
            {
                "mission_ref": "m1",
                "customer_ref": "c1",
                "consultant_ref": "k1",
                "billing_type": "TIME",
                "start": "2026-01-01",
                "planned_end": "2026-06-30",
                "actual_end": "2025-12-01",
            }
        )
    with pytest.raises(ValidationError):
        FixedPriceProject.model_validate(
            {
                "project_ref": "p1",
                "price": 1,
                "cost_budget": 1,
                "deadline": "2026-12-18",
                "risks": [{"risk_ref": "r1", "probability": 0.3, "impact_days_most_likely": 5, "impact_days_max": 2}],
            }
        )
