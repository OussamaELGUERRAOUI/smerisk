"""Formula-level routes: /v1/simulation, /v1/pert, /v1/bayes, etc. (§3 to §8)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import smerisk
from smerisk_service.routes import add_route, always

ASYNC_SIMULATIONS = 20_000
"""``simulate_aggregate`` answers directly up to this number of draws, as a job above (§1)."""


def _many_draws(body: BaseModel) -> bool:
    return int(getattr(body, "n_simulations", 0)) > ASYNC_SIMULATIONS


router = APIRouter(prefix="/v1", tags=["formulas"])
for path, func, summary in [
    ("/risk/measures", smerisk.risk_measures, "VaR, expected shortfall, and CFaR of a sample (§3.2)"),
    ("/pert/estimate", smerisk.pert_estimate, "Three-point PERT estimate (§4.1)"),
    ("/bayes/credibility", smerisk.credibility, "Bühlmann-Straub credibility (§5.1)"),
    ("/bayes/beta-binomial", smerisk.beta_binomial, "Beta-Binomial rate (§5.2)"),
    ("/survival/kaplan-meier", smerisk.kaplan_meier, "Kaplan-Meier survival (§6.1)"),
    ("/credit/markov-payments", smerisk.markov_payments, "Markov chain of payments (§6.2)"),
    ("/credit/expected-loss", smerisk.expected_loss, "Expected credit loss (§6.3)"),
    ("/audit/benford", smerisk.benford, "Benford's law (§7.1)"),
    ("/operations/newsvendor", smerisk.newsvendor, "Newsvendor order quantity (§7.2)"),
    ("/operations/erlang-c", smerisk.erlang_c, "Erlang C staffing (§7.3)"),
    ("/validation/brier", smerisk.brier, "Brier score and calibration (§8.1)"),
    ("/validation/kupiec", smerisk.kupiec, "Kupiec test (§8.2)"),
]:
    add_route(router, path, func, summary=summary)

add_route(
    router,
    "/simulation/aggregate",
    smerisk.simulate_aggregate,
    run_async=_many_draws,
    summary=f"Monte Carlo aggregate simulation (§3.1); a job above {ASYNC_SIMULATIONS:,} draws",
)
add_route(
    router,
    "/projects/fixed-price-completion",
    smerisk.fixed_price_completion,
    run_async=always,
    summary="Completion of a fixed-price project (§4.2); always a job",
)
add_route(
    router,
    "/utilization/forecast",
    smerisk.utilization_forecast,
    run_async=always,
    summary="Billable utilization forecast (§4.3); always a job",
)
