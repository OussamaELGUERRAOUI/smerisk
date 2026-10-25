from typing import Annotated, Any

import numpy as np
import pytest
from pydantic import Field
from scipy import stats

from smerisk import InvalidInput
from smerisk.core.distributions import Distribution, Fixed, Normal
from smerisk.core.types import month_from_index, month_index
from smerisk.core.warnings import WarningCode
from smerisk.prepare._base import Estimate
from smerisk.prepare.correlations import correlation_matrix, estimate_correlations, pair_key, shrunk_spearman
from smerisk.prepare.overrides import DerivedParameters


def series(values: Any, first: str = "2025-01") -> dict[str, float]:
    return {month_from_index(month_index(first) + i): float(v) for i, v in enumerate(values)}


RNG = np.random.default_rng(3)
A = RNG.normal(100, 10, 18)
B = A + RNG.normal(0, 8, 18)

# --- correlations ---------------------------------------------------------------------------------------------


def test_spearman_is_shrunk_toward_zero() -> None:
    estimate, warning = shrunk_spearman(series(A), series(B))
    rho = stats.spearmanr(A, B).statistic
    assert warning is None and estimate.source == "estimated"
    assert estimate.value == pytest.approx(rho * 18 / 28)
    assert (estimate.n_points, estimate.first, estimate.last) == (18, "2025-01", "2026-06")


def test_only_common_months_count() -> None:
    estimate, _ = shrunk_spearman(series(A), series(B[:14], first="2025-05"))  # common: 2025-05 to 2026-06
    assert estimate.n_points == 14
    assert estimate.value == pytest.approx(stats.spearmanr(A[4:], B[:14]).statistic * 14 / 24)


def test_short_or_constant_series_use_the_default() -> None:
    estimate, warning = shrunk_spearman(series(A[:11]), series(B[:11]), default=0.3, name="overtime~sick_pay")
    assert (estimate.value, estimate.source) == (0.3, "default")
    assert warning is not None and warning.code == WarningCode.DEFAULT_CORRELATION
    assert "overtime~sick_pay: 11 common months" in warning.message
    constant, warning = shrunk_spearman(series([5] * 18), series(B))
    assert constant.value == 0 and warning is not None and "constant" in warning.message


def test_matrix_from_pairs() -> None:
    pairs, warnings = estimate_correlations(
        {"overtime": series(A), "night": series(B), "sick_pay": series(A[:6])}, defaults={"sick_pay~overtime": 0.3}
    )
    assert list(pairs) == ["overtime~night", "overtime~sick_pay", "night~sick_pay"]
    assert pairs["overtime~sick_pay"].value == 0.3 and pairs["night~sick_pay"].value == 0
    assert len(warnings) == 2
    corr = correlation_matrix(["overtime", "night", "sick_pay"], {k: e.value for k, e in pairs.items()})
    m = np.array(corr.matrix)
    assert m[0, 2] == m[2, 0] == 0.3 and m[0, 1] == pytest.approx(pairs["overtime~night"].value)
    assert np.allclose(np.diag(m), 1)
    assert correlation_matrix(["a", "b"], {"b~a": 0.2}).matrix[0][1] == 0.2
    assert pair_key("a", "b") == "a~b"


# --- overrides ------------------------------------------------------------------------------------------------


def derived() -> DerivedParameters:
    params = DerivedParameters()
    params.add(
        "components.overtime",
        Estimate(value=Normal(mean=1800, sd=300, min=0), source="estimated", method="normal", n_points=14),
        Distribution,
    )
    params.default("components.planned_hires.probability", 0.8, schema=Annotated[float, Field(ge=0, le=1)])
    params.add(
        "correlation.overtime~sick_pay",
        Estimate(value=0.12, source="estimated", method="Spearman"),
        Annotated[float, Field(ge=-1, le=1)],
    )
    return params


def test_overrides_replace_values_and_are_traced() -> None:
    params = derived()
    params.apply(
        {
            "components.planned_hires.probability": 0.9,
            "correlation.sick_pay~overtime": 0.5,  # either order
            "components.overtime": {"type": "fixed", "value": 2000},
        }
    )
    assert params.value("components.planned_hires.probability") == 0.9
    assert params["correlation.overtime~sick_pay"].value == 0.5
    assert params.value("components.overtime") == Fixed(value=2000)
    assert {e.source for e in params.to_dict().values()} == {"override"}


def test_without_overrides_the_sources_are_kept() -> None:
    params = derived()
    params.apply({})
    assert [params[p].source for p in params] == ["estimated", "default", "estimated"]
    assert "components.overtime" in params and "components.night" not in params


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("components.night", 10, "unknown parameter"),
        ("unknown~pair", 0.1, "unknown parameter"),
        ("components.planned_hires.probability", 1.5, "less than or equal to 1"),
        ("correlation.overtime~sick_pay", -2, "greater than or equal to -1"),
        ("components.overtime", {"type": "normal", "mean": 1}, "Field required"),
    ],
)
def test_invalid_overrides(path: str, value: Any, message: str) -> None:
    params = derived()
    with pytest.raises(InvalidInput) as exc:
        params.apply({"components.planned_hires.probability": 0.9, path: value})
    (error,) = [e for e in exc.value.errors if e.path[-1] == path]
    assert error.path == ("settings", "overrides", path) and message in error.message
    assert all(params[p].source != "override" for p in params)
