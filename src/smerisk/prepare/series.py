"""Convert monthly series into distributions: aggregation, outliers, seasonality, and distribution selection (§11.1)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel
from scipy import stats

from smerisk.core.distributions import Distribution, Empirical, Fixed, Lognormal, Normal
from smerisk.core.envelope import Warning
from smerisk.core.errors import ComputationImpossible
from smerisk.core.types import month_index, month_range
from smerisk.core.warnings import WarningCode
from smerisk.prepare._base import Estimate

FloatArray = NDArray[np.float64]

OUTLIER_MADS = 5.0
MIN_MONTHS = 6
MIN_SEASONAL_MONTHS = 24
MIN_TREND_MONTHS = 24
MIN_BOOTSTRAP_MONTHS = 20
SKEWNESS_THRESHOLD = 1.0
TREND_SIGNIFICANCE = 0.05
_MAD_PER_MEAN_DEVIATION = 0.6745 / 0.7979
"""MAD ≈ 0.845 × mean absolute deviation for normal data: the fallback when most months are equal."""

Family = Literal["fixed", "lognormal", "normal", "empirical"]


def monthly_totals(items: Iterable[tuple[str, float]]) -> dict[str, float]:
    """Sum ``(month, amount)`` pairs by month, sorted. A month without any item is missing, never 0."""
    totals: dict[str, float] = defaultdict(float)
    for month, value in items:
        totals[month] += value
    return dict(sorted(totals.items()))


def missing_months(series: Mapping[str, float], first: str, last: str) -> list[str]:
    """Months from ``first`` to ``last`` that have no value."""
    return [m for m in month_range(first, last) if m not in series]


class Outlier(BaseModel):
    period: str
    value: float
    deviation: float
    """Distance to the median, in median absolute deviations."""


@dataclass
class SeriesFit:
    """A fitted series: call ``distribution(month)`` or ``estimate(month)`` for the distribution of a forecast month."""

    family: Family
    months: list[str]
    """Months used, after any exclusion of outliers."""
    values: FloatArray
    """Values used, deseasonalized and, with a trend, brought to the level of the last month."""
    nonnegative: bool
    seasonal_index: dict[int, float] | None = None
    """Calendar month (1–12) → mean of that month / overall mean."""
    trend_slope: float | None = None
    """Change per month, when a significant linear trend is applied."""
    outliers: list[Outlier] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """Steps that were skipped, for the method description."""

    def _scale(self, month: str | None) -> float:
        if self.seasonal_index is None or month is None:
            return 1.0
        return self.seasonal_index[int(month[5:])]

    def distribution(self, month: str | None = None) -> Distribution:
        """Distribution for ``month``; without a month, no seasonal index or trend is applied."""
        k = self._scale(month)
        x = self.values
        if self.trend_slope is not None and month is not None:
            x = x + self.trend_slope * (month_index(month) - month_index(self.months[-1]))
            if self.nonnegative:
                x = np.maximum(x, 0)
        x = x * k
        if k == 0 or self.family == "fixed":
            return Fixed(value=float(x.mean()))
        if self.family == "empirical":
            return Empirical(samples=x.tolist())
        if self.family == "lognormal":
            median, p90 = np.quantile(x, [0.5, 0.9])
            if 0 < median < p90:
                return Lognormal(median=float(median), p90=float(p90))
        return Normal(mean=float(x.mean()), sd=float(x.std(ddof=1)), min=0 if self.nonnegative else None)

    def estimate(self, month: str | None = None) -> Estimate[Distribution]:
        dist = self.distribution(month)
        steps = {
            "fixed": "fixed at the mean",
            "lognormal": "lognormal: observed median and 90th percentile",
            "normal": "normal: observed mean and standard deviation" + (", truncated at 0" if self.nonnegative else ""),
            "empirical": "empirical: observed months",
        }[dist.type]
        method = [steps, *self.notes]
        if month is not None and self.seasonal_index is not None:
            method.append(f"seasonal index {self._scale(month):.3f}")
        if month is not None and self.trend_slope is not None:
            method.append(f"linear trend {self.trend_slope:+.2f} per month")
        return Estimate(
            value=dist,
            source="estimated",
            method="; ".join(method),
            n_points=len(self.months),
            first=self.months[0],
            last=self.months[-1],
        )


def _outliers(months: list[str], x: FloatArray) -> list[Outlier]:
    deviation = np.abs(x - np.median(x))
    mad = float(np.median(deviation)) or float(deviation.mean()) * _MAD_PER_MEAN_DEVIATION
    if mad == 0:
        return []
    return [
        Outlier(period=m, value=float(v), deviation=float(d / mad))
        for m, v, d in zip(months, x, deviation, strict=True)
        if d > OUTLIER_MADS * mad
    ]


def fit_series(
    series: Mapping[str, float],
    *,
    nonnegative: bool = True,
    method: Literal["auto", "bootstrap"] = "auto",
    exclude_outliers: bool = False,
    trend: Literal["none", "linear"] = "none",
    name: str = "series",
) -> SeriesFit:
    """Fit the distribution of one component from its monthly values (§11.1).

    ``nonnegative``: the quantity cannot be negative (a normal distribution is then truncated at 0). ``name`` is used
    in warning messages.
    """
    if not series:
        raise ComputationImpossible("NO_DATA", f"{name}: no month with data")
    months = sorted(series)
    x = np.array([series[m] for m in months], dtype=np.float64)
    warnings: list[Warning] = []
    notes: list[str] = []

    # 1. Outliers: flagged, and dropped only on request.
    outliers = _outliers(months, x)
    if exclude_outliers and outliers:
        flagged = {o.period for o in outliers}
        x = np.array([v for m, v in zip(months, x, strict=True) if m not in flagged])
        months = [m for m in months if m not in flagged]
        notes.append(f"{len(outliers)} outlier month(s) excluded")
    n = len(x)

    # 2. Seasonality: multiplicative index by calendar month, then deseasonalized values.
    seasonal_index: dict[int, float] | None = None
    if n < MIN_SEASONAL_MONTHS:
        warnings.append(
            Warning(
                code=WarningCode.NO_SEASONALITY,
                message=f"{name}: {n} months, at least {MIN_SEASONAL_MONTHS} needed to estimate seasonality",
            )
        )
    elif x.min() < 0 or x.mean() <= 0:
        warnings.append(
            Warning(code=WarningCode.NO_SEASONALITY, message=f"{name}: negative values, seasonality not estimated")
        )
    else:
        calendar = np.array([int(m[5:]) for m in months])
        level: FloatArray = np.full(n, x.mean())
        if trend == "linear":
            # Ratio to the trend line rather than to the overall mean, or the trend would leak into the index.
            t = np.array([month_index(m) for m in months], dtype=np.float64)
            line = np.polyval(np.polyfit(t, x, 1), t)
            if line.min() > 0:
                level = np.asarray(line, dtype=np.float64)
        ratio = x / level
        seasonal_index = {c: float(ratio[calendar == c].mean()) if (calendar == c).any() else 1.0 for c in range(1, 13)}
        factor = np.array([seasonal_index[c] for c in calendar])
        # A calendar month that is always 0 (a bonus paid only in December) is forecast at 0 and fits nothing else.
        keep = factor > 0
        months = [m for m, k in zip(months, keep, strict=True) if k]
        x = x[keep] / factor[keep]
        n = len(x)

    # 3. Trend: only on request, with enough months and a significant slope.
    trend_slope: float | None = None
    if trend == "linear":
        t = np.array([month_index(m) for m in months], dtype=np.float64)
        if n < MIN_TREND_MONTHS:
            notes.append(f"trend not estimated ({n} months, at least {MIN_TREND_MONTHS} needed)")
        else:
            fit = stats.linregress(t, x)
            if fit.pvalue < TREND_SIGNIFICANCE:
                trend_slope = float(fit.slope)
                x = x + trend_slope * (t[-1] - t)
            else:
                notes.append(f"trend not significant (p = {fit.pvalue:.2f})")

    # 4. Distribution.
    family: Family
    if n < MIN_MONTHS:
        family = "fixed"
        warnings.append(
            Warning(code=WarningCode.SMALL_SAMPLE, message=f"{name}: {n} months, recommended minimum {MIN_MONTHS}")
        )
    elif method == "bootstrap" and n >= MIN_BOOTSTRAP_MONTHS:
        family = "empirical"
    else:
        if method == "bootstrap":
            warnings.append(
                Warning(
                    code=WarningCode.SMALL_SAMPLE,
                    message=f"{name}: {n} months, bootstrap needs {MIN_BOOTSTRAP_MONTHS}: parametric distribution used",
                )
            )
        spread = x.std() > 1e-9 * max(1.0, abs(float(x.mean())))  # deseasonalized data can be equal up to rounding
        skewness = float(stats.skew(x, bias=False)) if spread else 0.0
        family = "lognormal" if x.min() >= 0 and skewness > SKEWNESS_THRESHOLD else "normal"

    return SeriesFit(
        family=family,
        months=months,
        values=x,
        nonnegative=nonnegative,
        seasonal_index=seasonal_index,
        trend_slope=trend_slope,
        outliers=outliers,
        warnings=warnings,
        notes=notes,
    )
