import math
from typing import Any

import pytest
from scipy import stats

from smerisk import ComputationImpossible, InputTooLarge, InvalidInput, erlang_c, newsvendor

# --- newsvendor -----------------------------------------------------------------------------------------------

NORMAL = {"type": "normal", "mean": 100, "sd": 20}


def normal_loss(q: float, mean: float = 100, sd: float = 20) -> float:
    """E[(D − q)+] for D ~ N(mean, sd)."""
    z = (q - mean) / sd
    return float(sd * (stats.norm.pdf(z) - z * stats.norm.sf(z)))


def test_normal_demand_matches_closed_form() -> None:
    r = newsvendor(demand=NORMAL, unit_price=10, unit_cost=4).result
    assert r.critical_ratio == pytest.approx(6 / 10)
    assert r.optimal_quantity == math.ceil(100 + stats.norm.ppf(0.6) * 20)  # 105.07 → 106
    q = r.optimal_quantity
    assert r.expected_shortage == pytest.approx(normal_loss(q), abs=1e-4)
    assert r.expected_sales == pytest.approx(100 - normal_loss(q), abs=1e-4)
    assert r.expected_leftover == pytest.approx(q - r.expected_sales)
    assert r.expected_profit == pytest.approx(10 * r.expected_sales - 4 * q, abs=0.01)


def test_salvage_and_penalty_shift_the_ratio() -> None:
    r = newsvendor(demand=NORMAL, unit_price=10, unit_cost=4, salvage_value=1, shortage_penalty=2).result
    assert r.critical_ratio == pytest.approx(8 / (8 + 3))


def test_empirical_demand_is_exact() -> None:
    sales = [float(v) for v in range(80, 120, 2)]  # 20 past days: 80, 82, …, 118
    r = newsvendor(
        demand={"type": "empirical", "samples": sales}, unit_price=5, unit_cost=2, current_quantity=90
    ).result
    q = r.optimal_quantity
    assert r.expected_sales == pytest.approx(sum(min(s, q) for s in sales) / 20)
    assert r.comparison is not None and r.comparison.quantity == 90
    assert r.comparison.expected_sales == pytest.approx(sum(min(s, 90) for s in sales) / 20)
    assert r.expected_profit >= r.comparison.expected_profit


def test_optimum_beats_neighbouring_quantities() -> None:
    best = newsvendor(demand=NORMAL, unit_price=10, unit_cost=4).result
    for q in (best.optimal_quantity - 5, best.optimal_quantity + 5):
        other = newsvendor(demand=NORMAL, unit_price=10, unit_cost=4, current_quantity=q).result.comparison
        assert other is not None and other.expected_profit < best.expected_profit


def test_unprofitable_item_orders_nothing() -> None:
    r = newsvendor(demand=NORMAL, unit_price=3, unit_cost=4).result
    assert (r.critical_ratio, r.optimal_quantity, r.expected_profit) == (0.0, 0, 0.0)


def test_negative_demand_counts_as_zero() -> None:
    r = newsvendor(demand={"type": "normal", "mean": 1, "sd": 10}, unit_price=10, unit_cost=1).result
    assert r.expected_sales >= 0 and r.expected_shortage >= 0


def test_free_leftovers_have_no_finite_optimum() -> None:
    with pytest.raises(ComputationImpossible) as exc:
        newsvendor(demand=NORMAL, unit_price=10, unit_cost=4, salvage_value=4)
    assert exc.value.code == "NO_FINITE_OPTIMUM"


@pytest.mark.parametrize("field", ["unit_price", "unit_cost", "salvage_value", "shortage_penalty", "current_quantity"])
def test_newsvendor_rejects_negative_values(field: str) -> None:
    fields: dict[str, Any] = {"demand": NORMAL, "unit_price": 10, "unit_cost": 4, field: -1}
    with pytest.raises(InvalidInput) as exc:
        newsvendor(fields)
    assert exc.value.errors[0].path == (field,)


# --- erlang_c -------------------------------------------------------------------------------------------------


def test_erlang_c_by_hand() -> None:
    # A = 2, c = 3: P(wait) = (8/6 · 3) / (1 + 2 + 2 + 8/6 · 3) = 4/9.
    r = erlang_c(arrival_rate=2, service_time_minutes=60, agents=3).result
    assert r.offered_load == pytest.approx(2)
    assert r.p_wait == pytest.approx(4 / 9)
    assert r.occupancy == pytest.approx(2 / 3)
    assert r.average_wait_seconds == pytest.approx(4 / 9 / (3 - 2) * 3600)


def test_single_server_is_mm1() -> None:
    r = erlang_c(arrival_rate=30, service_time_minutes=1.5, agents=1, target_wait_seconds=60).result
    rho, spare = 0.75, 40 - 30
    assert r.p_wait == pytest.approx(rho)
    assert r.average_wait_seconds == pytest.approx(rho / spare * 3600)
    assert r.service_level == pytest.approx(1 - rho * math.exp(-spare / 60))


def test_recommendation_is_the_smallest_sufficient_team() -> None:
    r = erlang_c(arrival_rate=100, service_time_minutes=3, target_wait_seconds=20, target_service_level=0.8).result
    levels = {s.agents: s.service_level for s in r.table}
    assert r.recommended_agents == 8 and r.agents == 8
    assert levels[7] is not None and levels[7] < 0.8 <= (levels[8] or 0)
    assert [s.agents for s in r.table] == list(range(6, 11))  # first stable team (A = 5) to recommended + 2


def test_more_agents_means_less_waiting() -> None:
    table = erlang_c(arrival_rate=100, service_time_minutes=3, agents=6, target_wait_seconds=20).result.table
    waits = [s.p_wait for s in table]
    assert waits == sorted(waits, reverse=True)


def test_large_teams_are_numerically_stable() -> None:
    r = erlang_c(arrival_rate=6_000, service_time_minutes=6, target_wait_seconds=30, target_service_level=0.9).result
    assert r.recommended_agents is not None and 600 < r.recommended_agents < 700
    assert all(0 <= s.p_wait <= 1 for s in r.table)


def test_unstable_queue() -> None:
    with pytest.raises(ComputationImpossible) as exc:
        erlang_c(arrival_rate=100, service_time_minutes=3, agents=5)
    assert exc.value.code == "UNSTABLE_QUEUE" and exc.value.details["minimum_agents"] == 6


def test_erlang_c_invalid_and_limits() -> None:
    with pytest.raises(InvalidInput):
        erlang_c(arrival_rate=100, service_time_minutes=3, target_service_level=0.8)
    with pytest.raises(InvalidInput):
        erlang_c(arrival_rate=0, service_time_minutes=3)
    with pytest.raises(InputTooLarge):
        erlang_c(arrival_rate=1_000_000, service_time_minutes=60)
