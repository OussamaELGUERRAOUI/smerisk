from typing import Any

import pytest

from smerisk import InputTooLarge, InvalidInput, fixed_price_completion
from smerisk.core.warnings import WarningCode

FIXED_COST = {"type": "fixed", "value": 500}
IMPL = {"id": "implementation", "optimistic": 5, "most_likely": 8, "pessimistic": 15}
TEST = {"id": "testing", "optimistic": 2, "most_likely": 3, "pessimistic": 6}


def project(**changes: Any) -> dict[str, Any]:
    return {
        "contract_price": 12_000,
        "actual_cost": 3_000,
        "remaining_tasks": [IMPL, TEST],
        "daily_cost": FIXED_COST,
        "n_simulations": 100_000,
        "seed": 1,
        **changes,
    }


def pert_mean(t: dict[str, Any]) -> float:
    return float((t["optimistic"] + 4 * t["most_likely"] + t["pessimistic"]) / 6)


def pert_variance(t: dict[str, Any]) -> float:
    mean = pert_mean(t)
    return float((mean - t["optimistic"]) * (t["pessimistic"] - mean) / 7)


def test_symmetric_task_median_by_hand() -> None:
    r = fixed_price_completion(
        project(remaining_tasks=[{"id": "t", "optimistic": 4, "most_likely": 5, "pessimistic": 6}])
    ).result
    # Standard error of the median: 1.25 × sd / √n ≈ 0.0015 days ≈ 0.75 €.
    assert r.eac_quantiles["0.5"] == pytest.approx(3_000 + 5 * 500, abs=3)
    assert r.margin_quantiles["0.5"] == pytest.approx(12_000 - 5_500, abs=3)
    assert r.margin_rate_quantiles["0.5"] == pytest.approx(6_500 / 12_000, abs=3 / 12_000)


def test_expected_cost_matches_pert_and_risk_means() -> None:
    risk = {"id": "delay", "probability": 0.3, "impact_days": {"type": "triangular", "min": 1, "mode": 2, "max": 6}}
    out = fixed_price_completion(project(risks=[risk], quantiles=[0.5]))
    expected_days = pert_mean(IMPL) + pert_mean(TEST) + 0.3 * (1 + 2 + 6) / 3
    # The median of a right-skewed total sits slightly below its mean.
    assert 3_000 + 500 * (expected_days - 0.5) < out.result.eac_quantiles["0.5"] < 3_000 + 500 * expected_days


def test_contributions_are_variance_shares_of_independent_tasks() -> None:
    shares = {c.id: c.share for c in fixed_price_completion(project()).result.risk_contributions}
    v_impl, v_test = pert_variance(IMPL), pert_variance(TEST)
    assert shares["implementation"] == pytest.approx(v_impl / (v_impl + v_test), abs=0.01)
    assert sum(shares.values()) == pytest.approx(1)


def test_contributions_sum_to_one_with_uncertain_daily_cost() -> None:
    out = fixed_price_completion(project(daily_cost={"type": "normal", "mean": 500, "sd": 80, "min": 300}))
    assert sum(c.share for c in out.result.risk_contributions) == pytest.approx(1)


def test_adding_risks_keeps_task_draws_unchanged() -> None:
    certain = {"id": "certain", "probability": 1, "impact_days": {"type": "fixed", "value": 4}}
    never = {"id": "never", "probability": 0, "impact_days": {"type": "fixed", "value": 100}}
    base = fixed_price_completion(project()).result.eac_quantiles
    out = fixed_price_completion(project(risks=[certain, never])).result
    for level, value in out.eac_quantiles.items():
        assert value == pytest.approx(base[level] + 4 * 500, abs=0.01)
    assert {c.id: c.kind for c in out.risk_contributions} == {
        "implementation": "task",
        "testing": "task",
        "certain": "risk",
        "never": "risk",
    }
    assert [c.share for c in out.risk_contributions if c.kind == "risk"] == [0.0, 0.0]


def test_probabilities_at_the_extremes() -> None:
    # Remaining effort lies in [7, 21] days, so cost in [6,500, 13,500].
    low = fixed_price_completion(project(contract_price=6_000, cost_budget=14_000, deadline_days=21)).result
    assert (low.p_loss, low.p_within_budget, low.p_on_time) == (1.0, 1.0, 1.0)
    high = fixed_price_completion(project(contract_price=14_000, cost_budget=6_000, deadline_days=6.9)).result
    assert (high.p_loss, high.p_within_budget, high.p_on_time) == (0.0, 0.0, 0.0)


def test_team_capacity_scales_the_deadline() -> None:
    out = fixed_price_completion(project(deadline_days=10.5, team_capacity_days_per_period=2))
    assert out.result.p_on_time == 1.0
    assert out.assumptions["team_capacity_days_per_period"] == 2


def test_default_capacity_only_with_deadline() -> None:
    assert fixed_price_completion(project(deadline_days=12)).assumptions["team_capacity_days_per_period"] == 1.0
    out = fixed_price_completion(project())
    assert out.assumptions["team_capacity_days_per_period"] is None
    assert (out.result.p_within_budget, out.result.p_on_time) == (None, None)


def test_risks_only_project() -> None:
    risk = {"id": "rework", "probability": 0.5, "impact_days": {"type": "fixed", "value": 2}}
    r = fixed_price_completion(project(remaining_tasks=[], risks=[risk], quantiles=[0.05, 0.95])).result
    assert r.eac_quantiles == {"0.05": 3_000.0, "0.95": 4_000.0}
    assert [c.share for c in r.risk_contributions] == [1.0]


def test_same_seed_same_result() -> None:
    a = fixed_price_completion(project(n_simulations=25_000))
    assert a.result == fixed_price_completion(project(n_simulations=25_000)).result
    assert a.result != fixed_price_completion(project(n_simulations=25_000, seed=2)).result


def test_high_uncertainty_on_thin_margin() -> None:
    out = fixed_price_completion(project(contract_price=9_000, n_simulations=1_000))
    assert WarningCode.HIGH_UNCERTAINTY in [w.code for w in out.warnings]


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"contract_price": 0}, ("contract_price",)),
        ({"actual_cost": -1}, ("actual_cost",)),
        ({"team_capacity_days_per_period": 2}, ()),
        ({"remaining_tasks": [{**IMPL, "most_likely": 20}]}, ("remaining_tasks", 0)),
        (
            {"risks": [{"id": "r", "probability": 1.5, "impact_days": FIXED_COST}]},
            ("risks", 0, "probability"),
        ),
        ({"risks": [{"id": "testing", "probability": 0.5, "impact_days": FIXED_COST}]}, ("risks", 0, "id")),
        ({"remaining_tasks": [IMPL, IMPL]}, ("remaining_tasks", 1, "id")),
    ],
)
def test_invalid_input(changes: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        fixed_price_completion(project(**changes))
    assert path in [e.path for e in exc.value.errors]


def test_limits() -> None:
    with pytest.raises(InputTooLarge):
        fixed_price_completion(project(n_simulations=100_001))
    tasks = [{**TEST, "id": f"t{i}"} for i in range(201)]
    with pytest.raises(InputTooLarge):
        fixed_price_completion(project(remaining_tasks=tasks))
