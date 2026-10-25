import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError
from scipy import special, stats

from smerisk.core.distributions import (
    Z_90,
    Bernoulli,
    Beta,
    Distribution,
    Empirical,
    Fixed,
    Lognormal,
    Normal,
    Pert,
    Triangular,
    Uniform,
    from_normal,
    sample,
)

parse = TypeAdapter(Distribution).validate_python
Z = np.random.default_rng(0).standard_normal((1, 200_000))
U = special.ndtr(Z)


def test_fixed() -> None:
    assert np.all(from_normal([Fixed(value=3.5)], Z) == 3.5)


def test_normal_is_affine_in_z() -> None:
    np.testing.assert_allclose(from_normal([Normal(mean=100, sd=10)], Z), 100 + 10 * Z)


def test_truncated_normal_matches_scipy() -> None:
    x = from_normal([Normal(mean=100, sd=10, min=95, max=120)], Z)
    exact = stats.truncnorm.ppf(U, -0.5, 2, loc=100, scale=10)
    np.testing.assert_allclose(x, exact, atol=25 * 2e-6)
    assert x.min() >= 95 and x.max() <= 120


def test_normal_with_zero_sd_is_clipped_mean() -> None:
    assert np.all(from_normal([Normal(mean=10, sd=0, min=12)], Z) == 12)


def test_lognormal_median_and_p90() -> None:
    d = Lognormal(median=1840, p90=3100)
    x = from_normal([d, d, d], np.array([[0.0], [Z_90], [-Z_90]]))
    assert x[0, 0] == pytest.approx(1840)
    assert x[1, 0] == pytest.approx(3100)
    assert x[2, 0] == pytest.approx(1840**2 / 3100)


def test_triangular_matches_scipy() -> None:
    x = from_normal([Triangular(min=2, mode=3, max=7)], Z)
    np.testing.assert_allclose(x, stats.triang.ppf(U, 0.2, loc=2, scale=5), atol=1e-9)


@pytest.mark.parametrize(("a", "m", "b"), [(1, 3, 9), (0, 0, 1), (0, 1, 1), (2, 2.5, 30)])
def test_pert_matches_beta_pert(a: float, m: float, b: float) -> None:
    alpha, beta = 1 + 4 * (m - a) / (b - a), 1 + 4 * (b - m) / (b - a)
    x = from_normal([Pert(min=a, mode=m, max=b)], Z)
    np.testing.assert_allclose(x, a + (b - a) * special.betaincinv(alpha, beta, U), atol=(b - a) * 2e-6)


def test_pert_mean_is_classic_pert_formula() -> None:
    x = sample(Pert(min=1, mode=3, max=9), np.random.default_rng(1), 400_000)
    assert x.mean() == pytest.approx((1 + 4 * 3 + 9) / 6, abs=0.01)


@pytest.mark.parametrize(("alpha", "beta"), [(0.5, 0.5), (0.3, 4), (2, 3), (20, 2)])
def test_beta_matches_scipy(alpha: float, beta: float) -> None:
    np.testing.assert_allclose(
        from_normal([Beta(alpha=alpha, beta=beta)], Z), special.betaincinv(alpha, beta, U), atol=2e-6
    )


def test_uniform() -> None:
    np.testing.assert_allclose(from_normal([Uniform(min=-1, max=3)], Z), -1 + 4 * U)


@pytest.mark.parametrize("p", [0.0, 0.1, 0.5, 1.0])
def test_bernoulli_frequency(p: float) -> None:
    x = from_normal([Bernoulli(p=p, value=500)], Z)
    assert set(np.unique(x)) <= {0.0, 500.0}
    assert (x == 500).mean() == pytest.approx(p, abs=0.005)


def test_empirical_resamples_uniformly() -> None:
    values = [float(v) for v in range(20)]
    x = from_normal([Empirical(samples=values)], Z)
    assert set(np.unique(x)) == set(values)
    counts = np.bincount(x[0].astype(int))
    assert counts.min() / counts.max() > 0.9


def test_mixed_batch_keeps_row_order() -> None:
    dists: list[Distribution] = [Fixed(value=1), Normal(mean=5, sd=1), Fixed(value=2), Pert(min=0, mode=1, max=2)]
    z = np.random.default_rng(2).standard_normal((4, 1000))
    x = from_normal(dists, z)
    for i, d in enumerate(dists):
        np.testing.assert_array_equal(x[i], from_normal([d], z[i : i + 1])[0])


def test_discriminated_parsing() -> None:
    assert isinstance(parse({"type": "lognormal", "median": 10, "p90": 20}), Lognormal)


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "normal", "mean": 0, "sd": -1},
        {"type": "normal", "mean": 0, "sd": 1, "min": 2, "max": 1},
        {"type": "lognormal", "median": 10, "p90": 10},
        {"type": "triangular", "min": 1, "mode": 0, "max": 2},
        {"type": "pert", "min": 1, "mode": 1, "max": 1},
        {"type": "uniform", "min": 1, "max": 1},
        {"type": "bernoulli", "p": 1.5, "value": 1},
        {"type": "beta", "alpha": 0, "beta": 1},
        {"type": "empirical", "samples": [1.0] * 19},
        {"type": "fixed", "value": float("nan")},
        {"type": "fixed", "value": 1, "unknown": 2},
        {"type": "gamma", "shape": 1},
    ],
)
def test_invalid_parameters_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse(raw)


@given(
    lo=st.floats(-1e6, 1e6),
    width=st.floats(1e-3, 1e6),
    mode_at=st.floats(0, 1),
    z=st.lists(st.floats(-40, 40), min_size=1, max_size=50),
)
def test_bounded_laws_stay_within_bounds(lo: float, width: float, mode_at: float, z: list[float]) -> None:
    hi, mode = lo + width, lo + mode_at * width
    zz = np.array([z])
    for d in (Triangular(min=lo, mode=mode, max=hi), Pert(min=lo, mode=mode, max=hi), Uniform(min=lo, max=hi)):
        x = from_normal([d], zz)
        assert np.all(np.isfinite(x))
        assert np.all((x >= lo - 1e-9 * width) & (x <= hi + 1e-9 * width))
