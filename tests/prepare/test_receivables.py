import datetime as dt
from typing import Any

import pytest

from smerisk.contracts import Invoice, LedgerEntry
from smerisk.core.distributions import Lognormal
from smerisk.prepare.receivables import (
    invoice_state,
    invoices_from_ledger,
    late_payment_delay,
    month_ends,
    transitions,
)

AS_OF = dt.date(2026, 10, 20)


def invoice(ref: str, issue: str, due: str, amount: float, *payments: tuple[str, float], **fields: Any) -> Invoice:
    return Invoice.model_validate(
        {
            "invoice_ref": ref,
            "customer_ref": "c1",
            "issue_date": issue,
            "due_date": due,
            "amount": amount,
            "payments": [{"date": d, "amount": a} for d, a in payments],
            **fields,
        }
    )


INVOICES = [
    invoice("A", "2026-07-01", "2026-07-31", 1000, ("2026-09-10", 1000)),
    invoice("B", "2026-07-15", "2026-08-14", 500, ("2026-08-20", 200), written_off_date="2026-09-15"),
    invoice("C", "2026-08-05", "2026-09-04", 400),
    invoice("D", "2026-10-01", "2026-10-31", 250),
]


def test_month_ends() -> None:
    assert month_ends(dt.date(2026, 1, 15), dt.date(2026, 3, 30)) == [dt.date(2026, 1, 31), dt.date(2026, 2, 28)]
    assert month_ends(dt.date(2027, 12, 1), dt.date(2028, 2, 29))[-1] == dt.date(2028, 2, 29)


@pytest.mark.parametrize(
    ("date", "state"),
    [("2026-06-30", None), ("2026-07-31", "CURRENT"), ("2026-08-30", "LATE_30"), ("2026-08-31", "LATE_60")],
)
def test_invoice_state_by_days_late(date: str, state: str | None) -> None:
    assert invoice_state(INVOICES[0], dt.date.fromisoformat(date)) == state
    assert invoice_state(INVOICES[0], dt.date(2026, 10, 1)) == "PAID"
    assert invoice_state(invoice("E", "2026-01-01", "2026-01-31", 10), dt.date(2026, 4, 2)) == "LATE_90"


def test_transitions_weighted_by_amount_due() -> None:
    estimate = transitions(INVOICES, AS_OF, history_months=3)
    data = estimate.value
    assert [(t.from_, t.to, t.count) for t in data.transitions] == [
        ("CURRENT", "LATE_30", 900.0),  # B (500) and C (400)
        ("CURRENT", "LATE_60", 1000.0),  # A, more than 30 days late at the next month end
        ("LATE_30", "DEFAULT", 300.0),  # B, after a partial payment of 200
        ("LATE_60", "PAID", 1000.0),
    ]
    assert data.current_exposure == {"CURRENT": 250.0, "LATE_30": 0.0, "LATE_60": 400.0, "LATE_90": 0.0}
    assert (data.n_invoices, data.n_month_pairs) == (3, 2)
    assert (estimate.first, estimate.last) == ("2026-07-31", "2026-09-30")
    assert data.transitions[0].model_dump(by_alias=True)["from"] == "CURRENT"


def test_late_payment_delay() -> None:
    late = [
        invoice(
            f"L{d}", "2026-01-01", "2026-01-31", 100, ((dt.date(2026, 1, 31) + dt.timedelta(days=d)).isoformat(), 100)
        )
        for d in (10, 20, 30, 40, 100)
    ]
    on_time = invoice("T", "2026-01-01", "2026-01-31", 100, ("2026-02-03", 100))
    estimate = late_payment_delay([*late, on_time], AS_OF)
    assert estimate is not None and estimate.value == Lognormal(median=30, p90=76) and estimate.n_points == 5
    assert late_payment_delay(late[:4], AS_OF) is None


def entry(
    journal: str, number: str, date: str, account: str, debit: float = 0, credit: float = 0, **fields: Any
) -> LedgerEntry:
    return LedgerEntry(
        journal_code=journal,
        entry_number=number,
        entry_date=dt.date.fromisoformat(date),
        account=account,
        debit=debit,
        credit=credit,
        **fields,
    )


LEDGER = [
    entry("VT", "1", "2026-01-10", "411000", debit=1200, auxiliary_account="C1", document_ref="F1", lettering="AA"),
    entry("VT", "1", "2026-01-10", "706000", credit=1000),
    entry("VT", "1", "2026-01-10", "445710", credit=200),
    entry("BQ", "5", "2026-02-15", "512000", debit=1200),
    entry("BQ", "5", "2026-02-15", "411000", credit=1200, auxiliary_account="C1", lettering="AA"),
    entry("VT", "2", "2026-01-20", "411000", debit=600, auxiliary_account="C2", document_ref="F2", lettering="BB"),
    entry("VT", "2", "2026-01-20", "706000", credit=600),
    entry("OD", "9", "2026-06-30", "416000", debit=600, auxiliary_account="C2"),
    entry("OD", "9", "2026-06-30", "411000", credit=600, auxiliary_account="C2", lettering="BB"),
    entry("VT", "3", "2026-03-01", "411000", debit=300, auxiliary_account="C1", document_ref="F3"),
    entry("VT", "3", "2026-03-01", "706000", credit=300),
    entry("BQ", "6", "2026-03-05", "411000", credit=100, auxiliary_account="C1"),  # not lettered: not matched
]


def test_invoices_from_a_lettered_fec() -> None:
    invoices = {i.invoice_ref: i for i in invoices_from_ledger(LEDGER)}
    assert list(invoices) == ["F1", "F3", "F2"]
    f1 = invoices["F1"]
    assert (f1.customer_ref, f1.amount, f1.issue_date, f1.due_date) == (
        "C1",
        1200,
        dt.date(2026, 1, 10),
        dt.date(2026, 2, 9),
    )
    assert [(p.date, p.amount) for p in f1.payments] == [(dt.date(2026, 2, 15), 1200)]
    assert invoices["F3"].payments == [] and invoices["F3"].written_off_date is None
    assert invoices["F2"].written_off_date == dt.date(2026, 6, 30) and invoices["F2"].payments == []
    assert invoices_from_ledger(LEDGER, payment_terms_days=60)[0].due_date == dt.date(2026, 3, 11)
