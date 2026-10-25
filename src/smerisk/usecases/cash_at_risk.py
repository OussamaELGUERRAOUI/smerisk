"""cash-at-risk: bank balance over the coming weeks, with uncertain receipts and recurring payments (§12).

Model, week by week from the week of the day after ``as_of`` (its remaining days), over ``horizon`` ISO weeks:

- **Receipts**: each open invoice not yet late has three exclusive outcomes, drawn once for the whole horizon: paid
  on time (customer's on-time rate) in the week of its due date, paid late in the week of the median delay of late
  payments (with the probability that a late payment comes within the horizon), or not paid within the horizon. An
  invoice already late is paid in the week of the median remaining delay, with the probability conditional on the
  delay already reached; beyond 99% of the observed delays it is not expected. Invoices are grouped by customer and
  week; the receipts of customers of the same sector are correlated.
- **Recurring payments and other receipts** (FEC only): bank entries (accounts 512) by category of the counterpart
  account, as weekly totals over the history, weeks without any flow included. A category that also appears in
  ``scheduled_flows`` is not simulated: the known flows replace it.
- **Scheduled flows**: added as they are.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field
from scipy import stats

from smerisk.contracts import BankBalance, Invoice, LedgerEntry, ScheduledFlow, parse_records
from smerisk.core.distributions import Z_90, Distribution, Empirical, Fixed, Lognormal
from smerisk.core.envelope import InputModel, parse_input
from smerisk.core.errors import MAX_COMPONENTS
from smerisk.core.random import resolve_seed
from smerisk.core.types import Rate
from smerisk.formulas.simulation import AggregateResult, simulate_aggregate
from smerisk.prepare import (
    DerivedParameters,
    Estimate,
    QualityReport,
    correlation_matrix,
    invoices_from_ledger,
    late_payment_delay,
    on_time_rates,
    pair_key,
)
from smerisk.prepare.demand import EMPIRICAL_MIN_SAMPLES
from smerisk.prepare.rates import PAYMENT_GRACE_DAYS
from smerisk.prepare.receivables import outstanding
from smerisk.usecases._base import (
    Records,
    SimulationSettings,
    UsecaseResult,
    formula_warnings,
    one_of,
    require,
    respond,
)

CATEGORIES = {"401": "suppliers", "421": "payroll", "43": "social", "44": "tax", "16": "loans"}
"""Counterpart account prefix → category of a bank payment; other accounts are ``other``."""
BANK_ACCOUNTS = ("512",)
TRANSFER_ACCOUNTS = ("512", "58")
CUSTOMER_ACCOUNTS = ("411",)
DEFAULT_ON_TIME_PROBABILITY = 0.5
DEFAULT_LATE_DELAY = Lognormal(median=30, p90=90)
"""Days late of a late payment, when fewer than 5 late payments are known."""
LOST_BEYOND = 0.99
"""An invoice later than 99% of late payments is not expected within the horizon (``receivables-risk`` values it)."""
MIN_HISTORY_WEEKS = 26
POOLED = "other_customers"


class CashAtRiskSettings(SimulationSettings):
    horizon: int = Field(13, ge=1, le=60)
    """Weeks forecast."""
    threshold: float = 0.0
    """Balance below which the cash is short: the probability of crossing it is returned."""
    history_months: int = Field(12, ge=1, le=60)
    """FEC history used for recurring payments."""
    sector_correlation: float = Field(0.4, ge=-1, le=1)
    """Rank correlation between the receipts of customers of the same sector."""
    payment_terms_days: int = Field(30, ge=0, le=365)
    """FEC only: the due date of an invoice is its issue date plus these days."""


class CashAtRiskInput(InputModel):
    bank_balances: Records = Field(default_factory=list)
    invoices: Records = Field(default_factory=list)
    ledger_entries: Records = Field(default_factory=list)
    scheduled_flows: Records = Field(default_factory=list)
    settings: CashAtRiskSettings = Field(default_factory=CashAtRiskSettings)


def _week(date: dt.date) -> str:
    year, week, _ = date.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _monday(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


def _category(account: str) -> str:
    return next((name for prefix, name in CATEGORIES.items() if account.startswith(prefix)), "other")


def _cdf(delay: Distribution, days: float) -> float:
    """P(days late ≤ ``days``) for a lognormal or fixed delay."""
    if isinstance(delay, Lognormal):
        sigma = math.log(delay.p90 / delay.median) / Z_90
        return float(stats.lognorm.cdf(days, sigma, scale=delay.median)) if days > 0 else 0.0
    return float(days >= getattr(delay, "value", 0.0))


def _ppf(delay: Distribution, q: float) -> float:
    if isinstance(delay, Lognormal):
        sigma = math.log(delay.p90 / delay.median) / Z_90
        return float(stats.lognorm.ppf(min(q, 1 - 1e-9), sigma, scale=delay.median))
    return float(getattr(delay, "value", 0.0))


@dataclass(frozen=True)
class Receipt:
    week: str
    p_first: float
    """Probability of payment in ``week``: on time, or for a late invoice, within the horizon."""
    late_week: str | None = None
    p_by_late_week: float = 0.0
    """Probability of payment on time or late within the horizon (at least ``p_first``)."""


def _receipt(invoice: Invoice, p_on_time: float, delay: Distribution, as_of: dt.date, end: dt.date) -> Receipt | None:
    """Outcomes of an open invoice; ``None`` when it is not expected within the horizon."""
    late_by = (as_of - invoice.due_date).days
    if late_by <= PAYMENT_GRACE_DAYS:  # not late yet
        if invoice.due_date > end:
            return None
        on_time = max(invoice.due_date, as_of + dt.timedelta(days=1))
        late = min(invoice.due_date + dt.timedelta(days=_ppf(delay, 0.5)), end)
        p_late_within = _cdf(delay, (end - invoice.due_date).days)
        return Receipt(_week(on_time), p_on_time, _week(late), p_on_time + (1 - p_on_time) * p_late_within)
    reached = _cdf(delay, late_by)
    if reached >= LOST_BEYOND:
        return None
    p_within = (_cdf(delay, late_by + (end - as_of).days) - reached) / (1 - reached)
    median = invoice.due_date + dt.timedelta(days=_ppf(delay, reached + (1 - reached) / 2))
    return Receipt(_week(min(max(median, as_of + dt.timedelta(days=1)), end)), p_within)


def _bernoulli(p: float, value: float) -> dict[str, Any]:
    return {"type": "bernoulli", "p": min(max(p, 0.0), 1.0), "value": value}


def _receipt_components(name: str, amount: float, r: Receipt) -> list[dict[str, Any]]:
    """Components of a group of invoices, persistent so that an invoice is paid once at most.

    With z the group's draw: on time if z is in the top ``p_first``, late within the horizon if z is in the next band
    up to ``p_by_late_week``. The late week receives ``amount × 1[top p_by_late_week] − amount × 1[top p_first]``.
    """
    common: dict[str, Any] = {"sign": 1, "persistent": True, "correlation_group": name}
    if r.late_week is None or r.p_by_late_week <= r.p_first:
        return [{"id": name, **common, "by_period": {r.week: _bernoulli(r.p_first, amount)}}]
    if r.late_week == r.week:
        return [{"id": name, **common, "by_period": {r.week: _bernoulli(r.p_by_late_week, amount)}}]
    return [
        {
            "id": name,
            **common,
            "by_period": {r.week: _bernoulli(r.p_first, amount), r.late_week: _bernoulli(r.p_first, -amount)},
        },
        {"id": f"{name}.late", **common, "by_period": {r.late_week: _bernoulli(r.p_by_late_week, amount)}},
    ]


def _empirical(values: Sequence[float]) -> Distribution:
    if len(set(values)) == 1:
        return Fixed(value=values[0])
    return Empirical(samples=list(values) * math.ceil(EMPIRICAL_MIN_SAMPLES / len(values)))


def _bank_flows(entries: Sequence[LedgerEntry], first: dt.date, last: dt.date) -> dict[str, dict[str, float]]:
    """Category → week → amount of the bank entries between two dates (payments positive, receipts negative)."""
    by_entry: dict[tuple[str, str], list[LedgerEntry]] = defaultdict(list)
    for e in entries:
        if first <= e.entry_date <= last:
            by_entry[(e.journal_code, e.entry_number)].append(e)
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for lines in by_entry.values():
        bank = sum(e.balance for e in lines if e.account.startswith(BANK_ACCOUNTS))
        others = [e for e in lines if not e.account.startswith(BANK_ACCOUNTS)]
        if bank == 0 or not others:
            continue
        counterpart = max(others, key=lambda e: abs(e.balance))
        if counterpart.account.startswith(TRANSFER_ACCOUNTS):
            continue
        week = _week(lines[0].entry_date)
        if bank < 0:
            out[_category(counterpart.account)][week] += -bank
        elif not counterpart.account.startswith(CUSTOMER_ACCOUNTS):  # customer receipts come from the invoices
            out["other_receipts"][week] -= bank
    return {k: dict(v) for k, v in out.items()}


def cash_at_risk(
    params: CashAtRiskInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[AggregateResult]:
    started = time.perf_counter()
    inp = parse_input(CashAtRiskInput, params, fields)
    settings = inp.settings
    as_of = settings.as_of
    start = _monday(as_of + dt.timedelta(days=1))
    weeks = [_week(start + dt.timedelta(weeks=i)) for i in range(settings.horizon)]
    end = start + dt.timedelta(weeks=settings.horizon, days=-1)
    quality = QualityReport()

    balances = parse_records(BankBalance, inp.bank_balances, name="bank_balances")
    known = [b for b in balances.records if b.date <= as_of]
    require("bank_balances", len(known), "no balance at or before as_of")
    balance_date = max(b.date for b in known)
    opening = round(sum(b.amount for b in known if b.date == balance_date), 2)
    quality.records("bank_balances", balances, (b.date.isoformat() for b in known))
    scheduled = parse_records(ScheduledFlow, inp.scheduled_flows, name="scheduled_flows")
    flows = [f for f in scheduled.records if as_of < f.date <= end]
    quality.records("scheduled_flows", scheduled, (f.date.isoformat() for f in flows))

    name, rows = one_of(("invoices", inp.invoices), ("ledger_entries", inp.ledger_entries))
    entries: list[LedgerEntry] = []
    if name == "invoices":
        parsed_invoices = parse_records(Invoice, rows, name=name)
        invoices = [i for i in parsed_invoices.records if i.issue_date <= as_of]
        quality.records(name, parsed_invoices, (i.issue_date.isoformat() for i in invoices))
    else:
        parsed_entries = parse_records(LedgerEntry, rows, name=name)
        entries = [e for e in parsed_entries.records if e.entry_date <= as_of]
        quality.records(name, parsed_entries, (e.entry_date.isoformat() for e in entries))
        invoices = invoices_from_ledger(entries, payment_terms_days=settings.payment_terms_days)
    require(name, len(invoices), "no customer invoice issued before as_of")

    # --- Parameters ---------------------------------------------------------------------------------------------
    derived = DerivedParameters()
    open_invoices = [
        (i, round(outstanding(i, as_of), 2))
        for i in invoices
        if outstanding(i, as_of) > 0.005 and (i.written_off_date is None or i.written_off_date > as_of)
    ]
    company, customers = on_time_rates(invoices, as_of)
    if (company_mean := company.value.mean()) is not None:
        derived.add("receipts.on_time_probability", company.model_copy(update={"value": company_mean}), Rate)
    else:
        derived.default("receipts.on_time_probability", DEFAULT_ON_TIME_PROBABILITY, "no invoice due yet", Rate)
    for customer in sorted({i.customer_ref for i, _ in open_invoices} & set(customers)):
        rate = customers[customer]
        derived.add(
            f"receipts.on_time_probability.{customer}", rate.model_copy(update={"value": rate.value.mean()}), Rate
        )
    delay = late_payment_delay(invoices, as_of)
    if delay is not None:
        derived.add("receipts.late_delay_days", delay, Distribution)
    else:
        derived.default("receipts.late_delay_days", DEFAULT_LATE_DELAY, "fewer than 5 late payments", Distribution)

    replaced = {f.category.lower() for f in flows}
    if entries:
        history_start = as_of - dt.timedelta(days=round(settings.history_months * 365.25 / 12))
        first_monday = _monday(history_start + dt.timedelta(days=6))  # first complete week
        n_weeks = max((_monday(as_of + dt.timedelta(days=1)) - first_monday).days // 7, 0)
        history_weeks = [_week(first_monday + dt.timedelta(weeks=i)) for i in range(n_weeks)]
        quality.require("ledger_entries.weeks", MIN_HISTORY_WEEKS, len(history_weeks))
        if history_weeks:
            bank = _bank_flows(entries, history_start, as_of)
            for category, by_week in sorted(bank.items()):
                if category in replaced:
                    continue
                values = [by_week.get(w, 0.0) for w in history_weeks]
                derived.add(
                    f"recurring.{category}",
                    Estimate(
                        value=_empirical(values),
                        source="estimated",
                        method="weekly totals of the bank entries, weeks without flows included",
                        n_points=len(values),
                        first=history_weeks[0],
                        last=history_weeks[-1],
                    ),
                    Distribution,
                )
    derived.apply(settings.overrides)

    # --- Components ---------------------------------------------------------------------------------------------
    late_delay = derived.value("receipts.late_delay_days")
    # Invoices of a customer with the same outcomes are grouped: amount, and amount-weighted probabilities.
    buckets: dict[tuple[str, str, str | None], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    sectors: dict[str, str] = {}
    for invoice, amount in open_invoices:
        path = f"receipts.on_time_probability.{invoice.customer_ref}"
        p = derived.value(path) if path in derived else derived.value("receipts.on_time_probability")
        receipt = _receipt(invoice, p, late_delay, as_of, end)
        if receipt is None:
            continue
        bucket = buckets[(invoice.customer_ref, receipt.week, receipt.late_week)]
        bucket[0] += amount
        bucket[1] += amount * receipt.p_first
        bucket[2] += amount * receipt.p_by_late_week
        if invoice.customer_sector:
            sectors[invoice.customer_ref] = invoice.customer_sector

    components: list[dict[str, Any]] = []
    for path in sorted(p for p in derived if p.startswith("recurring.")):
        category = path.split(".", 1)[1]
        sign = 1 if category == "other_receipts" else -1
        components.append({"id": category, "sign": sign, "by_period": dict.fromkeys(weeks, derived.value(path))})
    if flows:
        totals: dict[str, float] = defaultdict(float)
        for f in flows:
            totals[_week(f.date)] += f.signed_amount
        components.append(
            {"id": "scheduled_flows", "sign": 1, "by_period": {w: Fixed(value=v) for w, v in totals.items()}}
        )

    # Within the component limit, the largest customers keep their own groups; the others are pooled by week.
    room = MAX_COMPONENTS - len(components) - len(weeks)
    grouped: dict[str, list[str]] = defaultdict(list)
    pooled: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    n_pooled = 0
    for (customer, week, late_week), (amount, p_first, p_late) in sorted(buckets.items(), key=lambda kv: -kv[1][0]):
        receipt = Receipt(week, p_first / amount, late_week, p_late / amount)
        name = f"receipts.{customer}.{week}" + (f".{late_week}" if late_week and late_week != week else "")
        parts = _receipt_components(name, amount, receipt)
        if len(parts) > room:
            pooled[week][0] += amount
            pooled[week][1] += max(p_first, p_late)
            n_pooled += 1
            continue
        room -= len(parts)
        components += parts
        if customer in sectors:
            grouped[sectors[customer]].append(name)
    for week, (amount, weighted) in sorted(pooled.items()):
        components += _receipt_components(f"receipts.{POOLED}.{week}", amount, Receipt(week, weighted / amount))
    if n_pooled:
        quality.outlier("invoices", POOLED, sum(a for a, _ in pooled.values()), float(n_pooled))
    require("invoices", len(components), "nothing to simulate: no open invoice, scheduled flow, or bank history")
    names = [n for members in grouped.values() if len(members) > 1 for n in members]
    pairs = {
        pair_key(a, b): settings.sector_correlation
        for members in grouped.values()
        for i, a in enumerate(members)
        for b in members[i + 1 :]
    }
    correlation = correlation_matrix(names, pairs) if names else None

    seed = resolve_seed(settings.seed)
    formula = simulate_aggregate(
        periods=weeks,
        opening_balance=opening,
        components=components,
        correlation=correlation,
        n_simulations=settings.n_simulations,
        seed=seed,
        confidence=settings.confidence,
        quantiles=settings.quantiles,
        threshold=settings.threshold,
    )
    return respond(
        formula.result,
        method="cash-at-risk",
        started=started,
        settings=settings,
        assumptions={
            **{k: v for k, v in formula.assumptions.items() if k != "correlation"},  # one row per receipt: too long
            "opening_balance_date": balance_date.isoformat(),
            "weeks": [weeks[0], weeks[-1]],
            "receipts": "open invoices: on time, late after the median delay, or not within the horizon; one draw "
            "per group of invoices for the whole horizon",
            "pooled_receipts": "smaller customers beyond the component limit, by week, single outcome",
            "replaced_by_scheduled_flows": sorted(replaced),
            "correlated_receipts": {sector: len(m) for sector, m in grouped.items() if len(m) > 1},
            "categories": {**CATEGORIES, "other": "other accounts"},
        },
        warnings=formula_warnings(formula),
        derived=derived,
        quality=quality,
    )
