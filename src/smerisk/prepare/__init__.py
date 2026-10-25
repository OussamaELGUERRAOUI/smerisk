"""§11: estimate the parameters of the formulas from raw data.

The estimators are deterministic. Each returns an ``Estimate``: the value, its source, the method, the number of
points used, and the period covered.
"""

from smerisk.prepare._base import Estimate, Source
from smerisk.prepare.correlations import correlation_matrix, estimate_correlations, pair_key, shrunk_spearman
from smerisk.prepare.demand import SlotArrivals, arrival_rates, daily_demand, demand_distribution
from smerisk.prepare.employment import SurvivalSubject, merge_contracts, survival_subjects
from smerisk.prepare.overrides import DerivedParameters
from smerisk.prepare.projects import actual_cost, team_daily_cost
from smerisk.prepare.quality import DataQuality, QualityReport
from smerisk.prepare.rates import (
    RateCounts,
    RatePrior,
    on_time_outcomes,
    on_time_rates,
    renewal_outcomes,
    renewal_rates,
    stage_order,
    stage_rates,
)
from smerisk.prepare.receivables import (
    ReceivablesData,
    invoice_state,
    invoices_from_ledger,
    late_payment_delay,
    transitions,
)
from smerisk.prepare.series import Outlier, SeriesFit, fit_series, missing_months, monthly_totals

__all__ = [
    "DataQuality",
    "DerivedParameters",
    "Estimate",
    "Outlier",
    "QualityReport",
    "RateCounts",
    "RatePrior",
    "ReceivablesData",
    "SeriesFit",
    "SlotArrivals",
    "Source",
    "SurvivalSubject",
    "actual_cost",
    "arrival_rates",
    "correlation_matrix",
    "daily_demand",
    "demand_distribution",
    "estimate_correlations",
    "fit_series",
    "invoice_state",
    "invoices_from_ledger",
    "late_payment_delay",
    "merge_contracts",
    "missing_months",
    "monthly_totals",
    "on_time_outcomes",
    "on_time_rates",
    "pair_key",
    "renewal_outcomes",
    "renewal_rates",
    "shrunk_spearman",
    "stage_order",
    "stage_rates",
    "survival_subjects",
    "team_daily_cost",
    "transitions",
]
