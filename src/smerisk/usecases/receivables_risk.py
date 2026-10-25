"""receivables-risk: ageing of receivables as a Markov chain, then the expected loss on open invoices (§12)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from smerisk.contracts import Invoice, LedgerEntry, parse_records
from smerisk.core.envelope import InputModel, parse_input
from smerisk.core.types import Level, Rate
from smerisk.formulas.credit import ExpectedLossResult, MarkovResult, expected_loss, markov_payments
from smerisk.prepare import DerivedParameters, Estimate, QualityReport, invoice_state, invoices_from_ledger, transitions
from smerisk.prepare.receivables import SMOOTHING, TRANSIENT, outstanding
from smerisk.usecases._base import Records, Settings, UsecaseResult, formula_warnings, one_of, require, respond

MIN_HISTORY_MONTHS = 24


class ReceivablesSettings(Settings):
    history_months: int = Field(24, ge=2, le=120)
    horizon: int = Field(6, ge=1, le=60)
    """Months projected by the Markov chain."""
    lgd: Rate = 1.0
    """Share of a defaulted amount that is lost."""
    payment_terms_days: int = Field(30, ge=0, le=365)
    """FEC only: the due date is the issue date plus these days."""
    n_simulations: int | None = Field(None, ge=1_000)
    """Also simulate the distribution of the loss (independent defaults)."""
    confidence: Level = 0.95


class ReceivablesRiskInput(InputModel):
    invoices: Records = Field(default_factory=list)
    ledger_entries: Records = Field(default_factory=list)
    """A lettered FEC instead of invoices: invoices are rebuilt from the customer accounts."""
    settings: ReceivablesSettings = Field(default_factory=ReceivablesSettings)


class ReceivablesRiskResult(BaseModel):
    ageing: MarkovResult
    expected_loss: ExpectedLossResult


def _invoices(inp: ReceivablesRiskInput, quality: QualityReport) -> list[Invoice]:
    settings = inp.settings
    name, rows = one_of(("invoices", inp.invoices), ("ledger_entries", inp.ledger_entries))
    if name == "invoices":
        parsed_invoices = parse_records(Invoice, rows, name=name)
        invoices = [i for i in parsed_invoices.records if i.issue_date <= settings.as_of]
        quality.records(name, parsed_invoices, (i.issue_date.isoformat() for i in invoices))
        return invoices
    parsed_entries = parse_records(LedgerEntry, rows, name=name)
    entries = [e for e in parsed_entries.records if e.entry_date <= settings.as_of]
    quality.records(name, parsed_entries, (e.entry_date.isoformat() for e in entries))
    return invoices_from_ledger(entries, payment_terms_days=settings.payment_terms_days)


def receivables_risk(
    params: ReceivablesRiskInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[ReceivablesRiskResult]:
    started = time.perf_counter()
    inp = parse_input(ReceivablesRiskInput, params, fields)
    settings = inp.settings
    quality = QualityReport()
    invoices = _invoices(inp, quality)
    require("invoices", len(invoices))

    data = transitions(invoices, settings.as_of, settings.history_months)
    require("invoices", len(data.value.transitions), "no invoice was open at two consecutive month ends")
    first_issue = min(i.issue_date for i in invoices)
    span = (settings.as_of.year - first_issue.year) * 12 + settings.as_of.month - first_issue.month
    quality.require("invoices.history_months", MIN_HISTORY_MONTHS, min(span, settings.history_months))
    derived = DerivedParameters()
    derived.add(
        "transitions",
        data.model_copy(update={"value": [t.model_dump(by_alias=True) for t in data.value.transitions]}),
        list[dict[str, Any]],
    )
    derived.add(
        "current_exposure",
        Estimate(value=data.value.current_exposure, source="estimated", method="amount still due by state"),
        dict[str, float],
    )
    derived.default("lgd", settings.lgd, reason="setting lgd", schema=Rate)
    derived.apply(settings.overrides)

    ageing = markov_payments(
        transitions=derived.value("transitions"),
        current_exposure=derived.value("current_exposure"),
        horizon_periods=settings.horizon,
        weight_by="amount",
        smoothing=SMOOTHING,
    )
    p_default = ageing.result.absorption.p_default_from
    exposures = [
        {
            "id": i.invoice_ref,
            "ead": round(outstanding(i, settings.as_of), 2),
            "pd": p_default[state],
            "lgd": derived.value("lgd"),
            "segment": i.customer_ref,
        }
        for i in invoices
        if (state := invoice_state(i, settings.as_of)) in TRANSIENT
    ]
    require("invoices", len(exposures), "no invoice is open at as_of")
    loss = expected_loss(
        exposures=exposures, n_simulations=settings.n_simulations, seed=settings.seed, confidence=settings.confidence
    )
    return respond(
        ReceivablesRiskResult(ageing=ageing.result, expected_loss=loss.result),
        method="receivables-risk",
        started=started,
        settings=settings,
        assumptions={
            "markov": ageing.assumptions,
            "expected_loss": loss.assumptions,
            "pd": "probability that an invoice in its current state ends in DEFAULT",
            "states": "LATE_30: 1 to 30 days past due, LATE_60: 31 to 60, LATE_90: more than 60",
        },
        warnings=formula_warnings(ageing, loss),
        derived=derived,
        quality=quality,
    )
