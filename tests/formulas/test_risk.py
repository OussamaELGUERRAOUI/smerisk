import numpy as np
import pytest

from smerisk import InputTooLarge, InvalidInput, risk_measures
from smerisk.formulas.risk import RiskMeasuresInput

SAMPLES = [float(v) for v in range(1, 101)]  # 1, 2, …, 100


def test_hand_computed_higher_is_better() -> None:
    r = risk_measures(samples=SAMPLES, confidence=0.95).result
    # Linear 5th percentile of 1..100: 1 + 0.05 × 99 = 5.95; worst cases: 1 to 5
    assert r.quantile == pytest.approx(5.95)
    assert r.var == pytest.approx(50.5 - 5.95)
    assert r.expected_shortfall == pytest.approx(3.0)
    assert r.mean == pytest.approx(50.5)
    assert r.sd == pytest.approx(np.std(SAMPLES, ddof=1))


def test_lower_is_better_is_the_mirror() -> None:
    r = risk_measures(samples=SAMPLES, orientation="lower_is_better").result
    assert r.quantile == pytest.approx(95.05)
    assert r.var == pytest.approx(95.05 - 50.5)
    assert r.expected_shortfall == pytest.approx(98.0)


def test_envelope() -> None:
    out = risk_measures({"samples": SAMPLES})
    assert out.assumptions == {"confidence": 0.95, "orientation": "higher_is_better", "quantile_method": "linear"}
    assert out.warnings == []
    assert out.meta.method == "empirical-quantile"


def test_too_few_samples() -> None:
    with pytest.raises(InvalidInput) as exc:
        risk_measures(samples=SAMPLES[:99])
    assert exc.value.errors[0].path == ("samples",)


def test_too_many_samples() -> None:
    with pytest.raises(InputTooLarge):
        risk_measures(samples=[0.0] * 50_001)


def test_params_and_fields_are_exclusive() -> None:
    with pytest.raises(TypeError):
        risk_measures({"samples": SAMPLES}, confidence=0.9)


def test_accepts_input_model() -> None:
    assert risk_measures(RiskMeasuresInput(samples=SAMPLES)).result == risk_measures(samples=SAMPLES).result
