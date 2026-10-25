from typing import Any

import pytest

from smerisk import InputTooLarge, InvalidInput, utilization_forecast

P6 = ["2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06"]
DAYS = {p: 20 for p in P6}


def consultant(id: str, mission: dict[str, Any] | None = None, days: dict[str, float] = DAYS) -> dict[str, Any]:
    return {"id": id, "available_days": days, "current_mission": mission}


def mission(end: str, days: float, p: float = 0.0, months: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"end_period": end, "billable_days_per_period": days, "renewal_probability": p}
    if months is not None:
        out["renewal_duration_months"] = {"type": "fixed", "value": months}
    return out


def deal(id: str, start: Any, need: int = 1, p: float = 1.0, days: float = 19, months: Any = 4) -> dict[str, Any]:
    return {
        "id": id,
        "win_probability": p,
        "start_period": start,
        "consultants_needed": need,
        "billable_days_per_period": days,
        "duration_months": months,
    }


def team(**changes: Any) -> dict[str, Any]:
    """c1 ends in February and renews for 3 months with p = 0.6; c2 is busy all along; c3 is free.
    Deal d1 (2 seats, 19 days a month, March to June) is won with p = 0.5."""
    return {
        "periods": P6,
        "consultants": [
            consultant("c1", mission("2027-02", 18, p=0.6, months=3)),
            consultant("c2", mission("2027-06", 20)),
            consultant("c3"),
        ],
        "deals": [deal("d1", "2027-03", need=2, p=0.5)],
        "n_simulations": 100_000,
        "seed": 1,
        **changes,
    }


def test_known_months_by_hand() -> None:
    r = utilization_forecast(team()).result
    jan = r.by_period[0]
    assert set(jan.utilization_quantiles.values()) == {round(38 / 60, 6)}
    assert set(jan.bench_days_quantiles.values()) == {20.0}
    bench = {c.id: c.p_on_bench for c in r.consultants}
    assert bench["c3"]["2027-01"] == 1.0
    assert set(bench["c2"].values()) == {0.0}


def test_renewal_and_win_probabilities() -> None:
    bench = {c.id: c.p_on_bench for c in utilization_forecast(team()).result.consultants}
    assert bench["c1"]["2027-03"] == pytest.approx(0.4 * 0.5, abs=0.005)  # not renewed and deal lost
    assert bench["c3"]["2027-03"] == pytest.approx(0.5, abs=0.005)  # deal lost
    assert bench["c1"]["2027-06"] == pytest.approx(0.5, abs=0.005)  # renewal over; on the deal if won


def test_unfilled_seat_is_filled_when_a_consultant_frees_up() -> None:
    # Won and c1 renewed (p = 0.3): one seat open March to May (3 × 19 days), filled by c1 in June.
    r = utilization_forecast(team(quantiles=[0.5, 0.9])).result.unstaffed_deal_days
    assert r.expected == pytest.approx(0.3 * 57, abs=0.4)
    assert r.quantiles == {"0.5": 0.0, "0.9": 57.0}


def test_billable_days_are_capped_by_availability() -> None:
    r = utilization_forecast(
        periods=["2027-01"],
        consultants=[consultant("c", mission("2027-01", 25), days={"2027-01": 15})],
        n_simulations=1_000,
    ).result
    assert r.by_period[0].utilization_quantiles["0.5"] == 1.0


def test_no_missions_means_full_bench() -> None:
    r = utilization_forecast(periods=P6, consultants=[consultant("a"), consultant("b")], n_simulations=1_000).result
    assert all(set(p.utilization_quantiles.values()) == {0.0} for p in r.by_period)
    assert all(set(p.bench_days_quantiles.values()) == {40.0} for p in r.by_period)


def test_beta_renewal_probability() -> None:
    m = {
        "end_period": "2027-01",
        "billable_days_per_period": 20,
        "renewal_probability_beta": {"alpha": 3, "beta": 1},
        "renewal_duration_months": {"type": "fixed", "value": 12},
    }
    r = utilization_forecast(periods=P6, consultants=[consultant("c", m)], n_simulations=100_000, seed=2).result
    assert r.consultants[0].p_on_bench["2027-02"] == pytest.approx(1 - 3 / 4, abs=0.005)


def test_earlier_start_has_priority_over_input_order() -> None:
    r = utilization_forecast(
        periods=P6,
        consultants=[consultant("c")],
        deals=[deal("late", "2027-02", months=2), deal("early", "2027-01", months=3)],
        n_simulations=1_000,
    ).result
    # "early" takes the only consultant January to March; "late" (February to March) is never staffed.
    assert r.unstaffed_deal_days.expected == 2 * 19


def test_input_order_breaks_ties_between_deals() -> None:
    r = utilization_forecast(
        periods=P6,
        consultants=[consultant("c")],
        deals=[deal("first", "2027-01", months=1, days=10), deal("second", "2027-01", months=1, days=15)],
        n_simulations=1_000,
    ).result
    assert r.unstaffed_deal_days.expected == 15


def test_consultant_free_longest_is_picked_first() -> None:
    r = utilization_forecast(
        periods=P6,
        consultants=[consultant("recent", mission("2027-01", 20)), consultant("long_free")],
        deals=[deal("d", "2027-03", months=1)],
        n_simulations=1_000,
    ).result
    bench = {c.id: c.p_on_bench["2027-03"] for c in r.consultants}
    assert bench == {"recent": 1.0, "long_free": 0.0}


def test_start_as_distribution_over_month_index() -> None:
    r = utilization_forecast(
        periods=P6,
        consultants=[consultant("c")],
        deals=[deal("d", {"type": "fixed", "value": 2}, months=1)],
        n_simulations=1_000,
    ).result
    assert [c.p_on_bench for c in r.consultants][0]["2027-03"] == 0.0
    assert [c.p_on_bench for c in r.consultants][0]["2027-02"] == 1.0


def test_threshold_probability() -> None:
    r = utilization_forecast(team(utilization_threshold=0.9)).result
    assert r.by_period[0].p_utilization_below == 1.0
    # March: below 0.9 unless the deal is won (utilization 0.95 or 0.97).
    assert r.by_period[2].p_utilization_below == pytest.approx(0.5, abs=0.005)


def test_adding_a_lost_deal_keeps_results_unchanged() -> None:
    base = team(n_simulations=25_000)
    extra = {**base, "deals": [*base["deals"], deal("never", "2027-01", p=0)]}
    assert utilization_forecast(base).result == utilization_forecast(extra).result


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"periods": ["2027-01", "2027-03"]}, ("periods", 1)),
        ({"periods": ["2027-W01"]}, ("periods", 0)),
        ({"consultants": [consultant("a"), consultant("a")]}, ("consultants", 1, "id")),
        ({"deals": [deal("d", "2027-01"), deal("d", "2027-02")]}, ("deals", 1, "id")),
        ({"consultants": [consultant("a", days={"2028-01": 5})]}, ("consultants", 0, "available_days", "2028-01")),
        ({"consultants": [consultant("a", days={"2027-01": 20})]}, ("periods", 1)),
        ({"deals": [deal("d", "2027-01", need=0)]}, ("deals", 0, "consultants_needed")),
    ],
)
def test_invalid_input(changes: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        utilization_forecast(team(**changes))
    assert path in [e.path for e in exc.value.errors]


@pytest.mark.parametrize(
    "bad_mission",
    [
        {"end_period": "2027-01", "billable_days_per_period": 5},
        {
            "end_period": "2027-01",
            "billable_days_per_period": 5,
            "renewal_probability": 0.5,
            "renewal_probability_beta": {"alpha": 1, "beta": 1},
            "renewal_duration_months": {"type": "fixed", "value": 1},
        },
        {"end_period": "2027-01", "billable_days_per_period": 5, "renewal_probability": 0.5},
    ],
)
def test_invalid_mission(bad_mission: dict[str, Any]) -> None:
    with pytest.raises(InvalidInput):
        utilization_forecast(team(consultants=[consultant("a", bad_mission)]))


def test_limits() -> None:
    with pytest.raises(InputTooLarge):
        utilization_forecast(team(n_simulations=100_001))
    with pytest.raises(InputTooLarge):
        utilization_forecast(team(consultants=[consultant(f"c{k}") for k in range(201)]))
