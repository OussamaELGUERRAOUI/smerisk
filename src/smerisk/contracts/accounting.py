"""LedgerEntry (FEC columns), Invoice, BankBalance, ScheduledFlow (§10.2)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from smerisk.contracts._base import Record, Ref

_FEC_DATE = re.compile(r"^\d{8}$")


def fec_date(value: Any) -> Any:
    """``AAAAMMJJ`` (FEC format) or ISO 8601; an empty string means no date."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if _FEC_DATE.match(value):
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def fec_amount(value: Any) -> Any:
    """Decimal comma and spaces accepted: ``"1 234,56"`` → 1234.56; an empty string means 0."""
    if isinstance(value, str):
        value = re.sub(r"[\s  ]", "", value).replace(",", ".")
        return value or 0
    return value


class LedgerEntry(Record):
    """One line of a FEC (fichier des écritures comptables), under its official column names.

    Labels (``EcritureLib``, ``CompAuxLib``, ``JournalLib``, ``CompteLib``) and the other FEC columns are accepted and
    dropped, so that a FEC can be sent as is without carrying names. The ``Montant`` + ``Sens`` variant of the format
    is converted to ``Debit`` / ``Credit``.
    """

    model_config = ConfigDict(extra="ignore", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    journal_code: Ref = Field(alias="JournalCode")
    entry_number: Ref = Field(alias="EcritureNum")
    entry_date: dt.date = Field(alias="EcritureDate")
    account: Ref = Field(alias="CompteNum")
    auxiliary_account: Ref | None = Field(None, alias="CompAuxNum")
    """Customer or supplier account."""
    document_ref: Ref | None = Field(None, alias="PieceRef")
    document_date: dt.date | None = Field(None, alias="PieceDate")
    debit: float = Field(alias="Debit")
    credit: float = Field(alias="Credit")
    lettering: Ref | None = Field(None, alias="EcritureLet")
    """Matching code: lines with the same code settle each other (an invoice and its payment)."""
    lettering_date: dt.date | None = Field(None, alias="DateLet")
    validation_date: dt.date | None = Field(None, alias="ValidDate")

    @model_validator(mode="before")
    @classmethod
    def _amount_and_direction(cls, data: Any) -> Any:
        if isinstance(data, dict) and "Montant" in data and "Debit" not in data and "debit" not in data:
            data = dict(data)
            amount, direction = data.pop("Montant"), str(data.pop("Sens", "")).strip().upper()
            if direction not in ("D", "C", "+1", "-1", "1"):
                raise ValueError("Sens must be D or C (or +1 / -1) when Montant is given")
            debit = direction in ("D", "+1", "1")
            data["Debit"], data["Credit"] = (amount, 0) if debit else (0, amount)
        return data

    @field_validator("auxiliary_account", "document_ref", "lettering", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("entry_date", "document_date", "lettering_date", "validation_date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> Any:
        return fec_date(value)

    @field_validator("debit", "credit", mode="before")
    @classmethod
    def _amount(cls, value: Any) -> Any:
        return fec_amount(value)

    @property
    def balance(self) -> float:
        """Debit − credit."""
        return self.debit - self.credit


class Payment(Record):
    date: dt.date
    amount: float = Field(gt=0)


class Invoice(Record):
    """A simpler alternative to the FEC for receivables."""

    invoice_ref: Ref
    customer_ref: Ref
    issue_date: dt.date
    due_date: dt.date
    amount: float = Field(gt=0)
    """Including tax; credit notes are not invoices."""
    payments: list[Payment] = Field(default_factory=list)
    written_off_date: dt.date | None = None
    """Written off, or moved to doubtful receivables."""
    customer_sector: Ref | None = None
    """Used to correlate the payments of customers in the same sector."""

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if self.due_date < self.issue_date:
            raise ValueError("due_date is before issue_date")
        if self.written_off_date is not None and self.written_off_date < self.issue_date:
            raise ValueError("written_off_date is before issue_date")
        return self


class BankBalance(Record):
    date: dt.date
    amount: float


class ScheduledFlow(Record):
    """A flow already known and dated: payroll schedule, tax due dates, loan repayments."""

    date: dt.date
    amount: float = Field(gt=0)
    direction: Literal["IN", "OUT"]
    category: Ref

    @property
    def signed_amount(self) -> float:
        return self.amount if self.direction == "IN" else -self.amount
