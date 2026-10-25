"""Convert monthly invoice states into a transition matrix (§11.3).

Also: invoices rebuilt from a lettered FEC, and the payment delay of invoices paid late.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from smerisk.contracts import Invoice, LedgerEntry, Payment
from smerisk.core.distributions import Distribution, Fixed, Lognormal
from smerisk.prepare._base import Estimate
from smerisk.prepare.rates import PAYMENT_GRACE_DAYS, paid_date

State = Literal["CURRENT", "LATE_30", "LATE_60", "LATE_90", "PAID", "DEFAULT"]
TRANSIENT: tuple[State, ...] = ("CURRENT", "LATE_30", "LATE_60", "LATE_90")
SMOOTHING = 0.5
MIN_LATE_PAYMENTS = 5

CUSTOMER_ACCOUNTS = ("411",)
WRITE_OFF_ACCOUNTS = ("416", "654")
"""An entry that clears a customer account against one of these accounts writes the invoice off."""
DEFAULT_PAYMENT_TERMS_DAYS = 30
"""The FEC has no due date: issue date + 30 days, the French legal default."""


def month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def month_ends(first: dt.date, last: dt.date) -> list[dt.date]:
    """Month ends from the month of ``first`` to ``last``, inclusive."""
    out = []
    year, month = first.year, first.month
    while (end := month_end(year, month)) <= last:
        out.append(end)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def outstanding(invoice: Invoice, date: dt.date) -> float:
    """Amount still due at ``date``."""
    return max(invoice.amount - sum(p.amount for p in invoice.payments if p.date <= date), 0.0)


def invoice_state(invoice: Invoice, date: dt.date) -> State | None:
    """State of the invoice at ``date``; ``None`` before it is issued.

    ``LATE_30``: 1 to 30 days past due, ``LATE_60``: 31 to 60, ``LATE_90``: more than 60.
    """
    if invoice.issue_date > date:
        return None
    if paid_date(invoice, date) is not None:
        return "PAID"
    if invoice.written_off_date is not None and invoice.written_off_date <= date:
        return "DEFAULT"
    late = (date - invoice.due_date).days
    if late <= 0:
        return "CURRENT"
    return "LATE_30" if late <= 30 else "LATE_60" if late <= 60 else "LATE_90"


class TransitionCount(BaseModel):
    """Input of ``markov_payments``: amounts moving from one month-end state to the next."""

    from_: State = Field(alias="from", serialization_alias="from")
    to: State
    count: float


class ReceivablesData(BaseModel):
    transitions: list[TransitionCount]
    current_exposure: dict[str, float]
    """Amount still due by state at the analysis date."""
    n_invoices: int
    n_month_pairs: int


def transitions(invoices: Sequence[Invoice], as_of: dt.date, history_months: int = 24) -> Estimate[ReceivablesData]:
    """Month-end to month-end transitions of the last ``history_months`` months, weighted by the amount due."""
    start_year, start_month = divmod(as_of.year * 12 + as_of.month - 1 - history_months, 12)
    ends = month_ends(dt.date(start_year, start_month + 1, 1), as_of)
    weights: dict[tuple[State, State], float] = defaultdict(float)
    used: set[str] = set()
    for invoice in invoices:
        for before, after in zip(ends, ends[1:], strict=False):
            state = invoice_state(invoice, before)
            if state not in TRANSIENT:
                continue
            nxt = invoice_state(invoice, after)
            assert state is not None and nxt is not None
            weights[(state, nxt)] += outstanding(invoice, before)
            used.add(invoice.invoice_ref)
    exposure: dict[str, float] = dict.fromkeys(TRANSIENT, 0.0)
    for invoice in invoices:
        state = invoice_state(invoice, as_of)
        if state in TRANSIENT:
            exposure[state] += outstanding(invoice, as_of)
    data = ReceivablesData(
        transitions=[
            TransitionCount.model_validate({"from": a, "to": b, "count": w}) for (a, b), w in sorted(weights.items())
        ],
        current_exposure={k: round(v, 2) for k, v in exposure.items()},
        n_invoices=len(used),
        n_month_pairs=max(len(ends) - 1, 0),
    )
    return Estimate(
        value=data,
        source="estimated",
        method=f"month-end states, weighted by amount due; smoothing {SMOOTHING} in the formula",
        n_points=len(used),
        first=ends[0].isoformat() if ends else None,
        last=ends[-1].isoformat() if ends else None,
    )


def late_payment_delay(invoices: Sequence[Invoice], as_of: dt.date) -> Estimate[Distribution] | None:
    """Days from due date to payment for invoices paid late: lognormal from the observed median and 90th percentile.

    ``None`` with fewer than 5 late payments: the use case then uses its default.
    """
    delays = []
    for invoice in invoices:
        paid = paid_date(invoice, as_of)
        if paid is not None and (paid - invoice.due_date).days > PAYMENT_GRACE_DAYS:
            delays.append(float((paid - invoice.due_date).days))
    if len(delays) < MIN_LATE_PAYMENTS:
        return None
    median, p90 = (float(q) for q in np.quantile(delays, [0.5, 0.9]))
    value: Distribution = Lognormal(median=median, p90=p90) if p90 > median else Fixed(value=median)
    return Estimate(
        value=value,
        source="estimated",
        method="lognormal: observed median and 90th percentile of days late",
        n_points=len(delays),
    )


# --- Invoices from a lettered FEC -----------------------------------------------------------------------------


def _is(account: str, prefixes: Sequence[str]) -> bool:
    return account.startswith(tuple(prefixes))


def invoices_from_ledger(
    entries: Sequence[LedgerEntry], *, payment_terms_days: int = DEFAULT_PAYMENT_TERMS_DAYS
) -> list[Invoice]:
    """Rebuild customer invoices from a FEC: debits on customer accounts (411), settled by lettering.

    An invoice is the debit on a customer account for one document (``PieceRef``, otherwise the entry number). When
    its lettering group is complete, it is paid at the date of the last credit of the group, or written off if that
    credit clears the account against 416 (doubtful receivables) or 654 (bad debts). Unlettered credits are not
    matched to any invoice. Provisions on account 491 are not linked to an invoice in a FEC and are not read.
    """
    by_entry: dict[tuple[str, str], list[LedgerEntry]] = defaultdict(list)
    for e in entries:
        by_entry[(e.journal_code, e.entry_number)].append(e)

    debits: dict[tuple[str, str], list[LedgerEntry]] = defaultdict(list)
    letter_credits: dict[tuple[str, str], list[LedgerEntry]] = defaultdict(list)
    for e in entries:
        if not _is(e.account, CUSTOMER_ACCOUNTS):
            continue
        customer = e.auxiliary_account or e.account
        if e.debit > 0:
            debits[(customer, e.document_ref or f"{e.journal_code}-{e.entry_number}")].append(e)
        if e.credit > 0 and e.lettering is not None:
            letter_credits[(customer, e.lettering)].append(e)

    invoices = []
    for (customer, ref), lines in sorted(debits.items()):
        amount = sum(e.debit for e in lines)
        issue = min(e.document_date or e.entry_date for e in lines)
        letters = {e.lettering for e in lines if e.lettering is not None}
        payments: list[Payment] = []
        written_off: dt.date | None = None
        if len(letters) == 1:
            credits = letter_credits.get((customer, letters.pop()), [])
            if credits:
                last = max(credits, key=lambda e: e.entry_date)
                others = by_entry[(last.journal_code, last.entry_number)]
                if any(_is(o.account, WRITE_OFF_ACCOUNTS) and o.debit > 0 for o in others):
                    written_off = last.entry_date
                else:
                    payments = [Payment(date=last.entry_date, amount=amount)]
        invoices.append(
            Invoice(
                invoice_ref=ref,
                customer_ref=customer,
                issue_date=issue,
                due_date=issue + dt.timedelta(days=payment_terms_days),
                amount=amount,
                payments=payments,
                written_off_date=written_off,
            )
        )
    return invoices
