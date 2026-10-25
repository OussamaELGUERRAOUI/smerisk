import datetime as dt
from typing import Any

import pytest

from smerisk.contracts import Deal, Invoice, Mission
from smerisk.prepare.rates import (
    RateCounts,
    RatePrior,
    on_time_outcomes,
    on_time_rates,
    renewal_outcomes,
    renewal_rates,
    stage_order,
    stage_prior,
    stage_rates,
)

AS_OF = dt.date(2026, 10, 20)


def mission(ref: str, customer: str, consultant: str, start: str, end: str, **fields: Any) -> Mission:
    return Mission.model_validate(
        {
            "mission_ref": ref,
            "customer_ref": customer,
            "consultant_ref": consultant,
            "start": start,
            "planned_end": end,
            "billing_type": "TIME",
            **fields,
        }
    )


MISSIONS = [
    mission("m1", "c1", "k1", "2026-01-05", "2026-03-31"),
    mission("m2", "c1", "k1", "2026-04-01", "2026-06-30", renewal_of="m1"),
    mission("m3", "c1", "k1", "2026-07-06", "2026-09-30"),  # 4 business days after m2: a continuation
    mission("m5", "c2", "k2", "2026-02-02", "2026-05-29"),
    mission("m4", "c2", "k2", "2026-06-15", "2026-10-16"),  # too late to continue m5; ended 2 business days ago
    mission("m7", "c3", "k3", "2026-09-01", "2027-02-26"),  # under way
]


def test_renewal_outcomes() -> None:
    outcomes = {m.mission_ref: success for m, success in renewal_outcomes(MISSIONS, AS_OF)}
    assert outcomes == {"m1": True, "m2": True, "m3": False, "m5": False}


def test_renewal_rates_shrink_customers_toward_the_company() -> None:
    company, customers = renewal_rates(MISSIONS, AS_OF)
    assert company.value == RateCounts(successes=2, trials=4, prior=RatePrior(mean=0.6, strength=5))
    assert company.value.mean() == pytest.approx(5 / 9)
    assert (company.first, company.last) == ("2026-03-31", "2026-09-30")
    c1 = customers["c1"].value
    assert (c1.successes, c1.trials) == (2, 3) and c1.prior is not None
    assert c1.prior.mean == pytest.approx(5 / 9) and c1.prior.strength == 5
    assert "fewer than 5 missions ended" in customers["c2"].method


def test_renewal_replays_a_past_date() -> None:
    outcomes = {m.mission_ref: s for m, s in renewal_outcomes(MISSIONS, dt.date(2026, 4, 2))}
    assert outcomes == {"m1": True}  # m2 has started; nothing else has ended


def test_renewal_without_any_ended_mission() -> None:
    company, customers = renewal_rates(MISSIONS[-1:], AS_OF, prior=RatePrior(mean=0.5, strength=2))
    assert company.value.trials == 0 and company.value.mean() == 0.5 and customers == {}
    assert company.first is None


# --- stages ---------------------------------------------------------------------------------------------------


def deal(ref: str, outcome: str, *path: str) -> Deal:
    history = [{"stage": s, "date": f"2026-0{i + 1}-01"} for i, s in enumerate(path)]
    return Deal.model_validate(
        {
            "deal_ref": ref,
            "customer_ref": "c1",
            "stage": path[-1],
            "stages_history": history,
            "amount": 50_000,
            "outcome": outcome,
        }
    )


DEALS = [
    deal("d1", "WON", "lead", "qualified", "proposal", "negotiation"),
    deal("d2", "LOST", "lead", "qualified"),
    deal("d3", "LOST", "lead"),
    deal("d4", "OPEN", "lead", "qualified", "proposal"),
]


def test_stage_order_is_inferred_from_histories() -> None:
    assert stage_order(DEALS) == ["lead", "qualified", "proposal", "negotiation"]


def test_stage_rates() -> None:
    rates = stage_rates(DEALS)
    assert [(r.value.successes, r.value.trials) for r in rates.values()] == [(1, 3), (1, 2), (1, 1), (1, 1)]
    assert [r.value.prior.mean for r in rates.values() if r.value.prior] == [0.10, 0.25, 0.50, 0.75]
    assert rates["lead"].method == "beta-binomial, prior 10% for stage 1 of 4, strength 4"
    assert (rates["qualified"].first, rates["qualified"].last) == ("2026-02-01", "2026-02-01")


def test_stage_priors_are_interpolated() -> None:
    assert [stage_prior(r, 3) for r in range(3)] == pytest.approx([0.10, 0.375, 0.75])
    assert stage_prior(0, 1) == 0.10
    rates = stage_rates(DEALS, stages=["lead", "qualified", "won"])
    assert rates["won"].value.trials == 0 and rates["won"].first is None


# --- payment --------------------------------------------------------------------------------------------------


def invoice(ref: str, customer: str, due: str, *payments: tuple[str, float], **fields: Any) -> Invoice:
    return Invoice.model_validate(
        {
            "invoice_ref": ref,
            "customer_ref": customer,
            "issue_date": fields.pop("issue_date", "2026-07-01"),
            "due_date": due,
            "amount": 1000,
            "payments": [{"date": d, "amount": a} for d, a in payments],
            **fields,
        }
    )


INVOICES = [
    invoice("i1", "c1", "2026-09-01", ("2026-09-04", 1000)),
    invoice("i2", "c1", "2026-09-01", ("2026-09-03", 600), ("2026-09-20", 400)),  # completed late
    invoice("i3", "c2", "2026-09-01"),
    invoice("i4", "c2", "2026-10-18"),  # due 2 days ago: not known yet
    invoice("i5", "c2", "2026-08-01", ("2026-10-25", 1000)),  # paid after the analysis date
    invoice("i6", "c2", "2026-12-01", issue_date="2026-11-01"),
]


def test_on_time_outcomes() -> None:
    outcomes = {i.invoice_ref: s for i, s in on_time_outcomes(INVOICES, AS_OF)}
    assert outcomes == {"i1": True, "i2": False, "i3": False, "i5": False}


def test_on_time_rates() -> None:
    company, customers = on_time_rates(INVOICES, AS_OF)
    assert (company.value.successes, company.value.trials, company.value.prior) == (1, 4, None)
    assert company.value.mean() == 0.25
    c1 = customers["c1"].value
    assert (c1.successes, c1.trials) == (1, 2) and c1.prior == RatePrior(mean=0.25, strength=5)
    assert RateCounts(successes=0, trials=0, prior=None).mean() is None
