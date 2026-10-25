from typing import Any

import numpy as np
import pytest

from smerisk import ComputationImpossible, InputTooLarge, InvalidInput, expected_loss, markov_payments
from smerisk.core.warnings import WarningCode

# --- markov_payments ------------------------------------------------------------------------------------------


def t(source: str, target: str, count: float) -> dict[str, Any]:
    return {"from": source, "to": target, "count": count}


SIMPLE = {
    "states": ["CURRENT", "PAID", "DEFAULT"],
    "transitions": [t("CURRENT", "CURRENT", 2), t("CURRENT", "PAID", 6), t("CURRENT", "DEFAULT", 2)],
    "current_exposure": {"CURRENT": 1000},
    "smoothing": 0,
    "horizon_periods": 2,
}

AGEING = [
    t("CURRENT", "CURRENT", 40),
    t("CURRENT", "PAID", 50),
    t("CURRENT", "LATE_30", 10),
    t("LATE_30", "PAID", 6),
    t("LATE_30", "LATE_60", 4),
    t("LATE_60", "PAID", 2),
    t("LATE_60", "LATE_90", 2),
    t("LATE_90", "PAID", 1),
    t("LATE_90", "LATE_90", 1),
    t("LATE_90", "DEFAULT", 1),
]


def test_single_state_by_hand() -> None:
    # P(CURRENT → CURRENT, PAID, DEFAULT) = (0.2, 0.6, 0.2); N = 1 / 0.8 = 1.25; B = 1.25 × (0.6, 0.2).
    r = markov_payments(SIMPLE).result
    assert r.transition_matrix["CURRENT"] == pytest.approx({"CURRENT": 0.2, "PAID": 0.6, "DEFAULT": 0.2})
    assert r.transition_matrix["PAID"] == {"CURRENT": 0.0, "PAID": 1.0, "DEFAULT": 0.0}
    assert r.absorption.p_paid_from["CURRENT"] == pytest.approx(0.75)
    assert r.absorption.p_default_from["CURRENT"] == pytest.approx(0.25)
    assert r.absorption.expected_months_to_resolution["CURRENT"] == pytest.approx(1.25)
    assert r.absorption.expected_months_to_payment["CURRENT"] == pytest.approx(1.25)
    assert r.expected_default_amount == 250.0
    m1, m2 = r.projection
    assert m1.balances == {"CURRENT": 200.0, "PAID": 600.0, "DEFAULT": 200.0}
    assert (m1.collections, m1.new_defaults) == (600.0, 200.0)
    assert (m2.balances["CURRENT"], m2.collections, m2.new_defaults) == (40.0, 120.0, 40.0)


def test_smoothing_adds_lambda_to_every_target() -> None:
    r = markov_payments({**SIMPLE, "smoothing": 0.5}).result
    assert r.transition_matrix["CURRENT"] == pytest.approx(
        {"CURRENT": 2.5 / 11.5, "PAID": 6.5 / 11.5, "DEFAULT": 2.5 / 11.5}
    )


def ageing(**changes: Any) -> dict[str, Any]:
    return {
        "transitions": AGEING,
        "current_exposure": {"CURRENT": 10_000, "LATE_30": 2_000, "LATE_90": 500},
        "horizon_periods": 24,
        **changes,
    }


def matrix(r: Any, states: list[str]) -> np.ndarray[Any, Any]:
    return np.array([[r.transition_matrix[a][b] for b in states] for a in states])


def test_absorption_matches_the_long_run_of_the_chain() -> None:
    r = markov_payments(ageing()).result
    states = list(r.transition_matrix)
    limit = np.linalg.matrix_power(matrix(r, states), 2_000)
    for i, s in enumerate(states[:4]):
        assert r.absorption.p_paid_from[s] == pytest.approx(limit[i, states.index("PAID")])
        assert r.absorption.p_default_from[s] == pytest.approx(limit[i, states.index("DEFAULT")])
        assert r.absorption.p_paid_from[s] + r.absorption.p_default_from[s] == pytest.approx(1)


def test_expected_months_to_payment_by_direct_summation() -> None:
    r = markov_payments(ageing()).result
    states = list(r.transition_matrix)
    p, paid = matrix(r, states), states.index("PAID")
    # E[T | paid] = sum_t t · P(first reach PAID at t) / P(paid)
    step, power, weighted = p.copy(), np.eye(len(states)), np.zeros(len(states))
    for month in range(1, 3_000):
        before = power[:, paid].copy()
        power = power @ step
        weighted += month * (power[:, paid] - before)
    for i, s in enumerate(states[:4]):
        assert r.absorption.expected_months_to_payment[s] == pytest.approx(weighted[i] / power[i, paid], rel=1e-6)


def test_projection_conserves_amounts() -> None:
    r = markov_payments(ageing()).result
    for month in r.projection:
        assert sum(month.balances.values()) == pytest.approx(12_500, abs=0.05)
    assert sum(m.collections for m in r.projection) == pytest.approx(r.projection[-1].balances["PAID"], abs=0.2)
    assert r.expected_default_amount == pytest.approx(
        sum(
            a * r.absorption.p_default_from[s] for s, a in {"CURRENT": 10_000, "LATE_30": 2_000, "LATE_90": 500}.items()
        ),
        abs=0.01,
    )


def test_state_never_absorbed_is_impossible() -> None:
    req = {
        "states": ["CURRENT", "STUCK", "PAID", "DEFAULT"],
        "transitions": [t("CURRENT", "PAID", 3), t("CURRENT", "STUCK", 1), t("STUCK", "STUCK", 5)],
        "current_exposure": {"CURRENT": 100},
        "smoothing": 0,
    }
    with pytest.raises(ComputationImpossible) as exc:
        markov_payments(req)
    assert exc.value.code == "NO_ABSORPTION" and exc.value.details["states"] == ["STUCK"]
    # A little smoothing opens an exit from STUCK.
    assert markov_payments({**req, "smoothing": 0.5}).result.absorption.p_paid_from["STUCK"] > 0


def test_state_without_data_or_smoothing_is_impossible() -> None:
    with pytest.raises(ComputationImpossible) as exc:
        markov_payments(transitions=[t("CURRENT", "PAID", 3)], current_exposure={}, smoothing=0)
    assert exc.value.code == "NO_TRANSITIONS_FROM_STATE"


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"transitions": [t("CURRENT", "LATE_120", 1)]}, ("transitions", 0, "to")),
        ({"transitions": [t("PAID", "CURRENT", 1)]}, ("transitions", 0, "from")),
        ({"current_exposure": {"PAID": 5}}, ("current_exposure", "PAID")),
        ({"current_exposure": {"CURRENT": -5}}, ("current_exposure", "CURRENT")),
        ({"states": ["CURRENT", "PAID"]}, ()),
        ({"transitions": [t("CURRENT", "PAID", -1)]}, ("transitions", 0, "count")),
    ],
)
def test_markov_invalid_input(changes: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        markov_payments(ageing(**changes))
    assert path in [e.path for e in exc.value.errors]


def test_markov_limits() -> None:
    with pytest.raises(InputTooLarge):
        markov_payments(ageing(horizon_periods=61))


# --- expected_loss --------------------------------------------------------------------------------------------


def exposure(id: str, ead: float, pd: Any, lgd: float = 1.0, segment: str | None = None) -> dict[str, Any]:
    return {"id": id, "ead": ead, "pd": pd, "lgd": lgd, "segment": segment}


PORTFOLIO = [
    exposure("a", 10_000, 0.02, 0.6, "trade"),
    exposure("b", 5_000, 0.10, 1.0, "trade"),
    exposure("c", 2_000, {"type": "beta", "alpha": 2, "beta": 8}, 1.0, "retail"),
    exposure("d", 800, 0.30),
    exposure("e", 300, 0.05),
    exposure("f", 100, 0.50),
]


def test_expected_loss_by_hand() -> None:
    r = expected_loss(exposures=PORTFOLIO).result
    losses = [10_000 * 0.02 * 0.6, 5_000 * 0.10, 2_000 * 0.2, 800 * 0.3, 300 * 0.05, 100 * 0.5]
    assert r.expected_loss == pytest.approx(sum(losses))
    assert r.by_segment == {"UNASSIGNED": 240 + 15 + 50, "retail": 400.0, "trade": 120 + 500}
    assert r.concentration == pytest.approx((sum(losses) - 15) / sum(losses))
    assert (r.loss_quantiles, r.expected_shortfall) == (None, None)


def test_simulated_mean_matches_expectation() -> None:
    many = [exposure(f"x{i}", 1_000 + 10 * i, 0.05 + 0.001 * i, 0.7) for i in range(100)]
    exact = expected_loss(exposures=many).result.expected_loss
    out = expected_loss(exposures=many, n_simulations=50_000, seed=1, quantiles=[0.5])
    assert out.result.loss_quantiles is not None
    assert out.result.loss_quantiles["0.5"] == pytest.approx(exact, rel=0.05)
    assert out.result.expected_shortfall is not None and out.result.expected_shortfall > exact


def test_single_exposure_simulation_by_hand() -> None:
    out = expected_loss(exposures=[exposure("a", 1_000, 0.1)], n_simulations=100_000, seed=3, quantiles=[0.5, 0.95])
    assert out.result.loss_quantiles == {"0.5": 0.0, "0.95": 1_000.0}
    assert out.result.expected_shortfall == 1_000.0
    assert WarningCode.HIGH_UNCERTAINTY in [w.code for w in out.warnings]


def test_beta_pd_is_drawn_per_simulation() -> None:
    beta = {"type": "beta", "alpha": 1, "beta": 4}  # mean 0.2
    many = [exposure(f"x{i}", 100, beta) for i in range(50)]
    out = expected_loss(exposures=many, n_simulations=20_000, seed=2, quantiles=[0.5])
    assert out.result.expected_loss == pytest.approx(50 * 100 * 0.2)
    assert out.result.loss_quantiles is not None
    assert out.result.loss_quantiles["0.5"] == pytest.approx(1_000, rel=0.1)


def test_adding_a_riskless_exposure_keeps_other_draws() -> None:
    base = expected_loss(exposures=PORTFOLIO, n_simulations=10_000, seed=4).result
    extra = expected_loss(exposures=[*PORTFOLIO, exposure("safe", 1_000, 0.0)], n_simulations=10_000, seed=4).result
    assert extra.loss_quantiles == base.loss_quantiles


@pytest.mark.parametrize(
    ("exposures", "path"),
    [
        ([exposure("a", 1, 0.1), exposure("a", 2, 0.1)], ("exposures", 1, "id")),
        ([exposure("a", -1, 0.1)], ("exposures", 0, "ead")),
        ([exposure("a", 1, 1.5)], ("exposures", 0, "pd")),
        ([exposure("a", 1, 0.1, lgd=1.2)], ("exposures", 0, "lgd")),
    ],
)
def test_expected_loss_invalid_input(exposures: list[dict[str, Any]], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        expected_loss(exposures=exposures)
    assert any(e.path[: len(path)] == path for e in exc.value.errors)


def test_expected_loss_simulation_limits() -> None:
    with pytest.raises(InvalidInput):
        expected_loss(exposures=PORTFOLIO, n_simulations=500)
    with pytest.raises(InputTooLarge):
        expected_loss(exposures=PORTFOLIO, n_simulations=100_001)
