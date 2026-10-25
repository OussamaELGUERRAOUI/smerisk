import math
from typing import Any

import pytest
from scipy import stats

from smerisk import ComputationImpossible, InvalidInput, brier, kupiec
from smerisk.core.warnings import WarningCode

# --- brier ----------------------------------------------------------------------------------------------------


def forecasts(*pairs: tuple[float, int]) -> list[dict[str, Any]]:
    return [{"p": p, "outcome": o} for p, o in pairs]


def test_brier_by_hand() -> None:
    data = forecasts((0.9, 1), (0.8, 1), (0.3, 0), (0.6, 0))
    r = brier(forecasts=data).result
    assert r.brier_score == pytest.approx((0.01 + 0.04 + 0.09 + 0.36) / 4)
    assert r.baseline_rate == 0.5
    assert r.reference_score == pytest.approx(0.25)
    assert r.skill_score == pytest.approx(1 - 0.125 / 0.25)


def test_given_baseline_rate() -> None:
    r = brier(forecasts=forecasts((0.9, 1), (0.1, 0)), baseline=0.3).result
    assert r.reference_score == pytest.approx((0.49 + 0.09) / 2)


def test_perfect_and_useless_forecasts() -> None:
    perfect = brier(forecasts=forecasts((1, 1), (0, 0))).result
    assert (perfect.brier_score, perfect.skill_score) == (0.0, 1.0)
    useless = brier(forecasts=forecasts((0.5, 1), (0.5, 0))).result
    assert useless.skill_score == pytest.approx(0)


def test_calibration_bins() -> None:
    data = forecasts((0.05, 0), (0.08, 0), (0.15, 1), (0.95, 1), (1.0, 1))
    bins = brier(forecasts=data, n_bins=10).result.calibration
    assert [(b.lower, b.count) for b in bins] == [(0.0, 2), (0.1, 1), (pytest.approx(0.9), 2)]
    assert bins[0].mean_forecast == pytest.approx(0.065) and bins[0].observed_frequency == 0
    assert bins[-1].upper == 1.0


def test_undefined_skill_score() -> None:
    with pytest.raises(ComputationImpossible) as exc:
        brier(forecasts=forecasts((0.7, 1), (0.6, 1)))
    assert exc.value.code == "UNDEFINED_SKILL_SCORE"


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"forecasts": forecasts((1.2, 1))}, ("forecasts", 0, "p")),
        ({"forecasts": [{"p": 0.5, "outcome": 2}]}, ("forecasts", 0, "outcome")),
        ({"forecasts": forecasts((0.5, 1)), "baseline": "persistence"}, ("baseline",)),
        ({"forecasts": forecasts((0.5, 1)), "n_bins": 0}, ("n_bins",)),
        ({"forecasts": []}, ("forecasts",)),
    ],
)
def test_brier_invalid_input(fields: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        brier(fields)
    assert any(e.path[: len(path)] == path for e in exc.value.errors)


# --- kupiec ---------------------------------------------------------------------------------------------------


def lr_by_hand(x: int, t: int, p: float) -> float:
    rate = x / t
    return -2 * ((t - x) * math.log(1 - p) + x * math.log(p)) + 2 * ((t - x) * math.log(1 - rate) + x * math.log(rate))


def test_kupiec_by_hand() -> None:
    r = kupiec(exceedances=20, observations=250, expected_rate=0.05).result
    assert r.observed_rate == 0.08
    assert r.lr == pytest.approx(lr_by_hand(20, 250, 0.05))
    assert r.p_value == pytest.approx(stats.chi2.sf(r.lr, 1))
    assert r.verdict == "TOO_MANY_EXCEEDANCES"


def test_expected_count_is_ok() -> None:
    out = kupiec(exceedances=12, observations=250, expected_rate=0.05)
    assert out.result.verdict == "OK" and out.warnings == []


def test_zero_exceedances_uses_the_limit() -> None:
    r = kupiec(exceedances=0, observations=250, expected_rate=0.05).result
    assert r.lr == pytest.approx(-2 * 250 * math.log(0.95))
    assert r.verdict == "TOO_FEW_EXCEEDANCES"


def test_all_exceedances_uses_the_limit() -> None:
    r = kupiec(exceedances=10, observations=10, expected_rate=0.05).result
    assert r.lr == pytest.approx(-2 * 10 * math.log(0.05))


def test_exact_rate_gives_zero_statistic() -> None:
    r = kupiec(exceedances=5, observations=100, expected_rate=0.05).result
    assert r.lr == pytest.approx(0, abs=1e-12) and r.p_value == pytest.approx(1)


def test_small_sample_warning() -> None:
    assert [w.code for w in kupiec(exceedances=1, observations=12, expected_rate=0.05).warnings] == [
        WarningCode.SMALL_SAMPLE
    ]


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"exceedances": 5, "observations": 4, "expected_rate": 0.05}, ()),
        ({"exceedances": -1, "observations": 4, "expected_rate": 0.05}, ("exceedances",)),
        ({"exceedances": 1, "observations": 0, "expected_rate": 0.05}, ("observations",)),
        ({"exceedances": 1, "observations": 4, "expected_rate": 0}, ("expected_rate",)),
    ],
)
def test_kupiec_invalid_input(fields: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        kupiec(fields)
    assert path in [e.path for e in exc.value.errors]
