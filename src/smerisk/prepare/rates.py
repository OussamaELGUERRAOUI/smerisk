"""Estimate renewal, stage-conversion, and on-time payment rates (§11.3).

Each rate is returned as counts and a prior, ready for ``beta_binomial`` (§5.2): the use case calls the formula.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence

import numpy as np
from pydantic import BaseModel, Field

from smerisk.contracts import Deal, Invoice, Mission
from smerisk.prepare._base import Estimate

GROUP_STRENGTH = 5.0
"""Weight, in observations, of the company-wide rate used as the prior of each customer."""
MIN_GROUP_TRIALS = 5
RENEWAL_PRIOR_MEAN = 0.6
RENEWAL_PRIOR_STRENGTH = 5.0
CONTINUATION_BUSINESS_DAYS = 5
PAYMENT_GRACE_DAYS = 5
STAGE_PRIORS = (0.10, 0.25, 0.50, 0.75)
"""Default win probability by stage rank, first to last; interpolated when there are not four stages."""
STAGE_STRENGTH = 4.0
_CENT = 0.005


class RatePrior(BaseModel):
    mean: float = Field(ge=0, le=1)
    strength: float = Field(gt=0)


class RateCounts(BaseModel):
    """Input of ``beta_binomial``: ``successes``, ``trials``, and a prior given by its mean and strength."""

    successes: int
    trials: int
    prior: RatePrior | None

    def mean(self) -> float | None:
        """Posterior mean; ``None`` without data or prior."""
        if self.prior is not None:
            k = self.prior.strength
            return (self.successes + self.prior.mean * k) / (self.trials + k)
        return self.successes / self.trials if self.trials else None


def _counts(outcomes: Iterable[bool]) -> tuple[int, int]:
    values = list(outcomes)
    return sum(values), len(values)


def _by_group(
    outcomes: Iterable[tuple[str, bool]], company: RateCounts, what: str, period: tuple[str | None, str | None]
) -> dict[str, Estimate[RateCounts]]:
    """One rate per group, shrunk toward the company-wide rate."""
    grouped: dict[str, list[bool]] = defaultdict(list)
    for group, success in outcomes:
        grouped[group].append(success)
    mean = company.mean()
    prior = RatePrior(mean=mean, strength=GROUP_STRENGTH) if mean is not None else None
    out: dict[str, Estimate[RateCounts]] = {}
    for group, values in sorted(grouped.items()):
        s, n = _counts(values)
        note = f", fewer than {MIN_GROUP_TRIALS} {what}" if n < MIN_GROUP_TRIALS else ""
        out[group] = Estimate(
            value=RateCounts(successes=s, trials=n, prior=prior),
            source="estimated",
            method=f"beta-binomial, prior = company-wide rate with strength {GROUP_STRENGTH:g}{note}",
            n_points=n,
            first=period[0],
            last=period[1],
        )
    return out


# --- Renewal of missions --------------------------------------------------------------------------------------


def _end(mission: Mission) -> dt.date:
    return mission.actual_end or mission.planned_end


def renewal_outcomes(missions: Sequence[Mission], as_of: dt.date) -> list[tuple[Mission, bool]]:
    """Missions that reached their end, and whether they were renewed.

    Renewed: another mission names it in ``renewal_of``, or the same consultant starts another mission at the same
    customer at most 5 business days after its end. A mission that ended less than 5 business days before ``as_of``
    and is not renewed yet is left out: its outcome is not known.
    """
    renewed = {m.renewal_of for m in missions if m.renewal_of is not None and m.start <= as_of}
    starts: dict[tuple[str, str], list[tuple[dt.date, str]]] = defaultdict(list)
    for m in missions:
        if m.start <= as_of:
            starts[(m.consultant_ref, m.customer_ref)].append((m.start, m.mission_ref))
    out = []
    for m in missions:
        end = _end(m)
        if end > as_of:
            continue
        limit = np.busday_offset(end, CONTINUATION_BUSINESS_DAYS, roll="forward").astype(dt.date)
        success = m.mission_ref in renewed or any(
            m.start < start <= limit and ref != m.mission_ref
            for start, ref in starts[(m.consultant_ref, m.customer_ref)]
        )
        if success or limit <= as_of:
            out.append((m, success))
    return out


def renewal_rates(
    missions: Sequence[Mission], as_of: dt.date, *, prior: RatePrior | None = None
) -> tuple[Estimate[RateCounts], dict[str, Estimate[RateCounts]]]:
    """Company-wide renewal rate, with ``prior`` (default: mean 0.6, strength 5), and one rate per customer."""
    prior = prior or RatePrior(mean=RENEWAL_PRIOR_MEAN, strength=RENEWAL_PRIOR_STRENGTH)
    outcomes = renewal_outcomes(missions, as_of)
    ends = sorted(_end(m).isoformat() for m, _ in outcomes)
    period = (ends[0], ends[-1]) if ends else (None, None)
    s, n = _counts(success for _, success in outcomes)
    company = RateCounts(successes=s, trials=n, prior=prior)
    estimate: Estimate[RateCounts] = Estimate(
        value=company,
        source="estimated",
        method=f"beta-binomial, prior mean {prior.mean:g} with strength {prior.strength:g}",
        n_points=n,
        first=period[0],
        last=period[1],
    )
    customers = _by_group(((m.customer_ref, success) for m, success in outcomes), company, "missions ended", period)
    return estimate, customers


# --- Win probability by stage ---------------------------------------------------------------------------------


def stage_order(deals: Sequence[Deal]) -> list[str]:
    """Stages ordered by their mean position in the deals' histories (the current stage comes last)."""
    positions: dict[str, list[int]] = defaultdict(list)
    first_seen: dict[str, int] = {}
    for deal in deals:
        path = [h.stage for h in sorted(deal.stages_history, key=lambda h: h.date)]
        if not path or path[-1] != deal.stage:
            path.append(deal.stage)
        for i, stage in enumerate(path):
            positions[stage].append(i)
            first_seen.setdefault(stage, len(first_seen))
    return sorted(positions, key=lambda s: (float(np.mean(positions[s])), first_seen[s]))


def stage_prior(rank: int, n_stages: int) -> float:
    """Default win probability of the stage of rank ``rank`` (0 = first) among ``n_stages``."""
    position = rank / (n_stages - 1) if n_stages > 1 else 0.0
    return float(np.interp(position, np.linspace(0, 1, len(STAGE_PRIORS)), STAGE_PRIORS))


def stage_rates(deals: Sequence[Deal], stages: Sequence[str] | None = None) -> dict[str, Estimate[RateCounts]]:
    """For each stage, the share of closed deals that passed through it and were won.

    ``stages``: the stages in pipeline order; by default inferred from the histories with ``stage_order``.
    """
    order = list(stages) if stages is not None else stage_order(deals)
    closed = [d for d in deals if d.outcome != "OPEN"]
    out: dict[str, Estimate[RateCounts]] = {}
    for rank, stage in enumerate(order):
        passed = [d for d in closed if d.stage == stage or any(h.stage == stage for h in d.stages_history)]
        s, n = _counts(d.outcome == "WON" for d in passed)
        prior = RatePrior(mean=stage_prior(rank, len(order)), strength=STAGE_STRENGTH)
        dates = sorted(h.date.isoformat() for d in passed for h in d.stages_history if h.stage == stage)
        out[stage] = Estimate(
            value=RateCounts(successes=s, trials=n, prior=prior),
            source="estimated",
            method=f"beta-binomial, prior {prior.mean:.0%} for stage {rank + 1} of {len(order)}, strength "
            f"{STAGE_STRENGTH:g}",
            n_points=n,
            first=dates[0] if dates else None,
            last=dates[-1] if dates else None,
        )
    return out


# --- Payment at the due date ----------------------------------------------------------------------------------


def paid_date(invoice: Invoice, as_of: dt.date) -> dt.date | None:
    """Date at which the payments received by ``as_of`` cover the invoice; ``None`` if not fully paid."""
    total = 0.0
    for payment in sorted(invoice.payments, key=lambda p: p.date):
        if payment.date > as_of:
            break
        total += payment.amount
        if total >= invoice.amount - _CENT:
            return payment.date
    return None


def on_time_outcomes(invoices: Sequence[Invoice], as_of: dt.date) -> list[tuple[Invoice, bool]]:
    """Invoices whose outcome is known, and whether they were paid at most 5 days after the due date."""
    out = []
    for invoice in invoices:
        if invoice.issue_date > as_of:
            continue
        limit = invoice.due_date + dt.timedelta(days=PAYMENT_GRACE_DAYS)
        paid = paid_date(invoice, as_of)
        if paid is not None or limit <= as_of:
            out.append((invoice, paid is not None and paid <= limit))
    return out


def on_time_rates(
    invoices: Sequence[Invoice], as_of: dt.date
) -> tuple[Estimate[RateCounts], dict[str, Estimate[RateCounts]]]:
    """Company-wide on-time payment rate, and one rate per customer shrunk toward it."""
    outcomes = on_time_outcomes(invoices, as_of)
    dues = sorted(i.due_date.isoformat() for i, _ in outcomes)
    period = (dues[0], dues[-1]) if dues else (None, None)
    s, n = _counts(success for _, success in outcomes)
    company = RateCounts(successes=s, trials=n, prior=None)
    estimate: Estimate[RateCounts] = Estimate(
        value=company, source="estimated", method="observed share", n_points=n, first=period[0], last=period[1]
    )
    customers = _by_group(((i.customer_ref, success) for i, success in outcomes), company, "invoices due", period)
    return estimate, customers
