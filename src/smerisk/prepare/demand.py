"""Convert sales into demand per product and arrivals per time slot (§11.3)."""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel

from smerisk.contracts import Sale
from smerisk.core.distributions import Distribution, Empirical, Normal
from smerisk.core.envelope import Warning
from smerisk.core.warnings import WarningCode
from smerisk.prepare._base import Estimate

ARRIVAL_WEEKS = 8
DEMAND_WEEKS = 12
MIN_DEMAND_DAYS = 8
EMPIRICAL_MIN_SAMPLES = 20
"""Minimum size of an ``empirical`` distribution (§2.4)."""
IRREGULAR_DISPERSION = 2.0
"""A slot whose variance exceeds twice its mean has irregular arrivals."""


def _window(sales: Sequence[Sale], as_of: dt.date, weeks: int, site_ref: str | None) -> list[Sale]:
    first = as_of - dt.timedelta(weeks=weeks)
    return [s for s in sales if first < s.date <= as_of and (site_ref is None or s.site_ref == site_ref)]


class SlotArrivals(BaseModel):
    site_ref: str
    weekday: int
    """0 = Monday."""
    hour: int
    arrival_rate: float
    """Mean tickets per hour: the ``arrival_rate`` of ``erlang_c``."""
    variance: float
    n_days: int
    irregular: bool
    """Variance above twice the mean: busier and quieter days than a Poisson flow would give."""


def arrival_rates(
    sales: Sequence[Sale], as_of: dt.date, *, weeks: int = ARRIVAL_WEEKS, site_ref: str | None = None
) -> list[SlotArrivals]:
    """Tickets by site, weekday, and hour over the last ``weeks`` weeks.

    Each day on which the site recorded tickets counts; an hour without a record on such a day counts as 0 tickets.
    Sales without ``hour`` or ``tickets`` are not used.
    """
    tickets: dict[tuple[str, dt.date, int], float] = defaultdict(float)
    days: dict[tuple[str, int], set[dt.date]] = defaultdict(set)
    hours: dict[tuple[str, int], set[int]] = defaultdict(set)
    for s in _window(sales, as_of, weeks, site_ref):
        if s.hour is None or s.tickets is None:
            continue
        tickets[(s.site_ref, s.date, s.hour)] += s.tickets
        days[(s.site_ref, s.date.weekday())].add(s.date)
        hours[(s.site_ref, s.date.weekday())].add(s.hour)
    out = []
    for site, weekday in sorted(days):
        dates = sorted(days[(site, weekday)])
        for hour in sorted(hours[(site, weekday)]):
            counts = np.array([tickets.get((site, d, hour), 0.0) for d in dates])
            mean = float(counts.mean())
            variance = float(counts.var(ddof=1)) if len(counts) > 1 else 0.0
            out.append(
                SlotArrivals(
                    site_ref=site,
                    weekday=weekday,
                    hour=hour,
                    arrival_rate=mean,
                    variance=variance,
                    n_days=len(dates),
                    irregular=variance > IRREGULAR_DISPERSION * mean,
                )
            )
    return out


def daily_demand(
    sales: Sequence[Sale],
    product_ref: str,
    as_of: dt.date,
    *,
    weekday: int | None = None,
    weeks: int = DEMAND_WEEKS,
    site_ref: str | None = None,
) -> dict[dt.date, float]:
    """Quantity of the product sold each day (on ``weekday`` only, if given) over the last ``weeks`` weeks.

    A day on which the site sold something but not this product counts as 0.
    """
    window = [s for s in _window(sales, as_of, weeks, site_ref) if weekday is None or s.date.weekday() == weekday]
    out = {s.date: 0.0 for s in window}
    for s in window:
        if s.product_ref == product_ref:
            out[s.date] += s.quantity
    return dict(sorted(out.items()))


def demand_distribution(
    demand: dict[dt.date, float], *, name: str = "demand"
) -> tuple[Estimate[Distribution], list[Warning]] | None:
    """Empirical distribution of the daily quantities; with fewer than 8 days, a normal distribution truncated at 0.

    Sales are what was sold, not what was asked for: a day that sold out understates the demand. ``None`` without
    any day.
    """
    if not demand:
        return None
    values = list(demand.values())
    n = len(values)
    days = sorted(demand)
    warnings = []
    dist: Distribution
    if n >= MIN_DEMAND_DAYS:
        # Repeating the whole list leaves the distribution unchanged and reaches the minimum size of `empirical`.
        dist = Empirical(samples=values * math.ceil(EMPIRICAL_MIN_SAMPLES / n))
        method = "empirical: observed days"
    else:
        sd = float(np.std(values, ddof=1)) if n > 1 else 0.0
        dist = Normal(mean=float(np.mean(values)), sd=sd, min=0)
        method = "normal: observed mean and standard deviation, truncated at 0"
        warnings.append(
            Warning(code=WarningCode.SMALL_SAMPLE, message=f"{name}: {n} days, recommended minimum {MIN_DEMAND_DAYS}")
        )
    estimate: Estimate[Distribution] = Estimate(
        value=dist, source="estimated", method=method, n_points=n, first=days[0].isoformat(), last=days[-1].isoformat()
    )
    return estimate, warnings
