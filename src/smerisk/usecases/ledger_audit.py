"""ledger-audit: Benford's test on the amounts of selected ledger accounts (§12)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field

from smerisk.contracts import LedgerEntry, parse_records
from smerisk.core.envelope import InputModel, parse_input
from smerisk.formulas.audit import MIN_FOR_CONCLUSION, BenfordResult, benford
from smerisk.prepare import DerivedParameters, Estimate, QualityReport
from smerisk.usecases._base import Records, Settings, UsecaseResult, formula_warnings, require, respond


class LedgerAuditSettings(Settings):
    accounts: list[str] = Field(default_factory=lambda: ["6", "2"], min_length=1)
    """Account prefixes to analyse; by default purchases and expenses (class 6) and fixed assets (class 2)."""
    min_amount: float = Field(10, ge=0)
    test: Literal["first_digit", "first_two_digits"] = "first_digit"


class LedgerAuditInput(InputModel):
    ledger_entries: Records = Field(default_factory=list)
    """FEC lines, as dictionaries with the FEC column names or as ``LedgerEntry`` (for example from ``read_fec``)."""
    settings: LedgerAuditSettings = Field(default_factory=LedgerAuditSettings)


def ledger_audit(
    params: LedgerAuditInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[BenfordResult]:
    started = time.perf_counter()
    inp = parse_input(LedgerAuditInput, params, fields)
    settings = inp.settings
    parsed = parse_records(LedgerEntry, inp.ledger_entries, name="ledger_entries")
    entries = [e for e in parsed.records if e.entry_date <= settings.as_of]
    require("ledger_entries", len(entries))
    quality = QualityReport()
    quality.records("ledger_entries", parsed, (e.entry_date.isoformat() for e in entries))

    # One amount per line: a line is either a debit or a credit; the test reads the absolute value.
    selected = [e for e in entries if e.account.startswith(tuple(settings.accounts)) and e.balance != 0]
    amounts = [abs(e.balance) for e in selected]
    labels = [f"{e.journal_code}-{e.entry_number}" for e in selected]
    eligible = sum(a >= settings.min_amount for a in amounts)
    quality.require("ledger_entries.eligible_amounts", MIN_FOR_CONCLUSION, eligible)
    require("ledger_entries", len(amounts), "no line on the selected accounts")

    dates = sorted(e.entry_date.isoformat() for e in selected)
    derived = DerivedParameters()
    derived.add(
        "amounts",
        Estimate(
            value=amounts,
            source="estimated",
            method=f"absolute amount of the lines on accounts {', '.join(settings.accounts)}",
            n_points=len(amounts),
            first=dates[0],
            last=dates[-1],
        ),
        list[float],
    )
    derived.apply(settings.overrides)
    overridden = derived["amounts"].source == "override"
    formula = benford(
        amounts=derived.value("amounts"),
        labels=None if overridden else labels,
        min_amount=settings.min_amount,
        test=settings.test,
    )
    return respond(
        formula.result,
        method="ledger-audit",
        started=started,
        settings=settings,
        assumptions={**formula.assumptions, "labels": "journal code and entry number"},
        warnings=formula_warnings(formula),
        derived=derived,
        quality=quality,
    )
