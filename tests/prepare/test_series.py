import numpy as np
import pytest

from smerisk import ComputationImpossible
from smerisk.core.distributions import Empirical, Fixed, Lognormal, Normal
from smerisk.core.types import month_from_index, month_index, month_range
from smerisk.core.warnings import WarningCode
from smerisk.prepare.series import fit_series, missing_months, monthly_totals


def months(n: int, first: str = "2024-01") -> list[str]:
    return [month_from_index(month_index(first) + i) for i in range(n)]


def series(values: list[float], first: str = "2024-01") -> dict[str, float]:
    return dict(zip(months(len(values), first), values, strict=True))


def codes(fit: object) -> list[WarningCode]:
    return [w.code for w in fit.warnings]  # type: ignore[attr-defined]


SYMMETRIC = [100, 104, 96, 102, 98, 101, 99, 103, 97, 100, 105, 95]
SKEWED = [40, 42, 45, 41, 44, 43, 60, 46, 48, 120, 47, 50]


def test_month_helpers() -> None:
    assert month_from_index(month_index("2026-12")) == "2026-12"
    assert month_range("2026-11", "2027-02") == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert month_range("2027-02", "2026-11") == []


def test_monthly_totals_sum_and_keep_gaps() -> None:
    totals = monthly_totals([("2026-02", 10), ("2026-01", 5), ("2026-02", 2.5), ("2026-04", 0)])
    assert totals == {"2026-01": 5, "2026-02": 12.5, "2026-04": 0}
    assert missing_months(totals, "2026-01", "2026-05") == ["2026-03", "2026-05"]


def test_few_months_give_a_fixed_value() -> None:
    fit = fit_series(series([10, 20, 30]), name="overtime")
    assert fit.distribution() == Fixed(value=20)
    assert codes(fit) == [WarningCode.NO_SEASONALITY, WarningCode.SMALL_SAMPLE]
    assert "overtime: 3 months" in fit.warnings[1].message


def test_symmetric_series_gives_a_truncated_normal() -> None:
    fit = fit_series(series(SYMMETRIC))
    dist = fit.distribution()
    assert isinstance(dist, Normal) and dist.min == 0
    assert dist.mean == pytest.approx(np.mean(SYMMETRIC)) and dist.sd == pytest.approx(np.std(SYMMETRIC, ddof=1))
    assert fit_series(series(SYMMETRIC), nonnegative=False).distribution().min is None  # type: ignore[union-attr]


def test_skewed_positive_series_gives_a_lognormal() -> None:
    dist = fit_series(series(SKEWED)).distribution()
    assert dist == Lognormal(median=float(np.median(SKEWED)), p90=float(np.quantile(SKEWED, 0.9)))


def test_skewed_series_with_negative_values_stays_normal() -> None:
    values = [v - 45 for v in SKEWED]
    assert isinstance(fit_series(series(values), nonnegative=False).distribution(), Normal)


def test_outliers_are_flagged_and_kept_unless_excluded() -> None:
    values = [*SYMMETRIC[:11], 400]
    fit = fit_series(series(values))
    assert [(o.period, o.value) for o in fit.outliers] == [("2024-12", 400)]
    median = np.median(values)
    mad = np.median(np.abs(np.array(values) - median))
    assert fit.outliers[0].deviation == pytest.approx((400 - median) / mad)
    assert len(fit.months) == 12
    excluded = fit_series(series(values), exclude_outliers=True)
    assert len(excluded.months) == 11 and "1 outlier month(s) excluded" in excluded.estimate().method


def test_outliers_when_most_months_are_equal() -> None:
    fit = fit_series(series([0] * 11 + [500]))  # MAD = 0: the mean absolute deviation is used instead
    assert [o.period for o in fit.outliers] == ["2024-12"]
    assert fit_series(series([7] * 12)).outliers == []


def test_seasonal_index_is_reapplied_to_the_forecast_month() -> None:
    shape = [0.8, 0.9, 1.0, 1.1, 1.2, 1.0, 0.7, 0.6, 1.0, 1.1, 1.2, 1.4]
    fit = fit_series(series([100 * shape[i % 12] for i in range(36)]))
    assert fit.seasonal_index is not None and codes(fit) == []
    assert fit.seasonal_index[12] == pytest.approx(1.4 / np.mean(shape))
    for month, s in (("2027-12", 1.4), ("2027-08", 0.6)):
        dist = fit.distribution(month)
        assert isinstance(dist, Normal) and dist.mean == pytest.approx(100 * s)
    assert fit.distribution().mean == pytest.approx(100 * np.mean(shape))  # type: ignore[union-attr]
    assert "seasonal index 1.400" in fit.estimate("2027-12").method.replace("1.4000", "1.400")


def test_month_always_zero_is_forecast_at_zero() -> None:
    fit = fit_series(series([1200.0 if (i + 1) % 12 == 0 else 0.0 for i in range(24)]))  # December bonus
    assert fit.distribution("2026-12") == Fixed(value=1200)
    assert fit.distribution("2026-05") == Fixed(value=0)


def test_negative_values_have_no_seasonality() -> None:
    fit = fit_series(series([(-1) ** i * 10.0 + 1 for i in range(24)]), nonnegative=False)
    assert fit.seasonal_index is None and codes(fit) == [WarningCode.NO_SEASONALITY]


def test_bootstrap() -> None:
    values = [float(v) for v in [*range(100, 112), *range(111, 99, -1)]]  # every calendar month averages 105.5
    fit = fit_series(series(values), method="bootstrap")
    assert fit.seasonal_index == {c: pytest.approx(1) for c in range(1, 13)}
    dist = fit.distribution()
    assert isinstance(dist, Empirical) and sorted(dist.samples) == pytest.approx(sorted(values))
    short = fit_series(series(SYMMETRIC), method="bootstrap")
    assert isinstance(short.distribution(), Normal) and WarningCode.SMALL_SAMPLE in codes(short)


def test_significant_linear_trend() -> None:
    rng = np.random.default_rng(1)
    values = [1000 + 20 * i + float(rng.normal(0, 5)) for i in range(30)]
    fit = fit_series(series(values), trend="linear")
    assert fit.trend_slope == pytest.approx(20, abs=1)
    last = fit.months[-1]
    in_three = month_from_index(month_index(last) + 3)
    assert fit.seasonal_index is not None and all(abs(k - 1) < 0.02 for k in fit.seasonal_index.values())
    k_last, k_three = (fit.seasonal_index[int(m[5:])] for m in (last, in_three))
    mean_last = fit.distribution(last).mean  # type: ignore[union-attr]
    mean_three = fit.distribution(in_three).mean  # type: ignore[union-attr]
    assert mean_three / k_three == pytest.approx(mean_last / k_last + 3 * fit.trend_slope)
    assert mean_last == pytest.approx(1000 + 20 * 29, abs=10)
    assert "linear trend" in fit.estimate(in_three).method


def test_trend_is_not_applied_without_evidence() -> None:
    rng = np.random.default_rng(2)
    flat = fit_series(series([100 + float(rng.normal(0, 5)) for _ in range(30)]), trend="linear")
    assert flat.trend_slope is None and "trend not significant" in flat.estimate().method
    short = fit_series(series(SYMMETRIC), trend="linear")
    assert short.trend_slope is None and "trend not estimated (12 months" in short.estimate().method
    assert fit_series(series([100 + 20.0 * i for i in range(30)])).trend_slope is None  # not requested


def test_estimate_describes_the_data() -> None:
    est = fit_series(series(SKEWED, first="2025-03")).estimate()
    assert (est.source, est.n_points, est.first, est.last) == ("estimated", 12, "2025-03", "2026-02")
    assert est.method == "lognormal: observed median and 90th percentile"


def test_no_data() -> None:
    with pytest.raises(ComputationImpossible) as exc:
        fit_series({})
    assert exc.value.code == "NO_DATA"


def test_seasonality_and_trend_together() -> None:
    shape = np.array([0.8, 0.9, 1.0, 1.1, 1.2, 1.0, 0.7, 0.6, 1.0, 1.1, 1.2, 1.4])
    shape = shape / shape.mean()
    fit = fit_series(series([(1000 + 20 * i) * shape[i % 12] for i in range(30)]), trend="linear")
    assert fit.seasonal_index is not None and fit.trend_slope == pytest.approx(20, rel=0.05)
    assert [fit.seasonal_index[c] for c in range(1, 13)] == pytest.approx(list(shape), abs=0.02)
    assert fit.distribution("2026-12").mean == pytest.approx((1000 + 20 * 35) * shape[11], rel=0.02)  # type: ignore[union-attr]
