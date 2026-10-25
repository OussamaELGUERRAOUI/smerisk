import math
from typing import Any

import pytest
from scipy import special

from smerisk import ComputationImpossible, InputTooLarge, InvalidInput, simulate_aggregate
from smerisk.core.warnings import WarningCode

P2 = ["2027-01", "2027-02"]


def normal(mean: float, sd: float) -> dict[str, Any]:
    return {"type": "normal", "mean": mean, "sd": sd}


def component(id: str, sign: int, dist: dict[str, Any], periods: list[str] = P2, **extra: Any) -> dict[str, Any]:
    return {"id": id, "sign": sign, "by_period": {p: dist for p in periods}, **extra}


def two_normals(**settings: Any) -> dict[str, Any]:
    """Receipts N(100, 10) − payments N(50, 5) over two independent months."""
    return {
        "periods": P2,
        "components": [component("in", 1, normal(100, 10)), component("out", -1, normal(50, 5))],
        "n_simulations": 100_000,
        "seed": 1,
        **settings,
    }


def test_sum_of_normals_matches_exact_solution() -> None:
    out = simulate_aggregate(two_normals())
    sd = math.sqrt(2 * (10**2 + 5**2))  # Horizon balance: N(100, sd)
    z95 = float(special.ndtri(0.95))
    end = out.result.by_period[-1]
    assert end.expected_cumulative == pytest.approx(100, abs=0.25)
    assert end.cumulative_quantiles["0.05"] == pytest.approx(100 - z95 * sd, abs=0.5)
    assert end.cumulative_quantiles["0.95"] == pytest.approx(100 + z95 * sd, abs=0.5)
    assert out.result.by_period[0].expected_flow == pytest.approx(50, abs=0.2)
    assert out.result.horizon.cfar == pytest.approx(z95 * sd, abs=0.5)
    # Expected Shortfall of a normal distribution: mu − sd · φ(z) / (1 − α)
    es = 100 - sd * math.exp(-(z95**2) / 2) / math.sqrt(2 * math.pi) / 0.05
    assert out.result.horizon.expected_shortfall == pytest.approx(es, abs=0.6)


def test_contributions_are_variance_shares() -> None:
    shares = {c.id: c.share for c in simulate_aggregate(two_normals()).result.contributions}
    assert shares["in"] == pytest.approx(0.8, abs=0.01)
    assert shares["out"] == pytest.approx(0.2, abs=0.01)
    assert sum(shares.values()) == pytest.approx(1)


def test_same_seed_same_result() -> None:
    a, b = simulate_aggregate(two_normals(seed=7)), simulate_aggregate(two_normals(seed=7))
    assert a.result == b.result
    assert a.result != simulate_aggregate(two_normals(seed=8)).result


def test_drawn_seed_is_returned_and_replayable() -> None:
    first = simulate_aggregate(two_normals(seed=None, n_simulations=1_000))
    seed = first.assumptions["seed"]
    assert isinstance(seed, int)
    assert simulate_aggregate(two_normals(seed=seed, n_simulations=1_000)).result == first.result


def test_reproducible_across_chunks() -> None:
    # 25,000 draws = 3 blocks; results depend only on the input and seed.
    a = simulate_aggregate(two_normals(n_simulations=25_000))
    assert a.result == simulate_aggregate(two_normals(n_simulations=25_000)).result


def test_threshold_probabilities_with_known_flows() -> None:
    out = simulate_aggregate(
        periods=["2027-W01", "2027-W02", "2027-W03"],
        opening_balance=5,
        components=[
            {
                "id": "flux",
                "sign": 1,
                "by_period": {
                    "2027-W01": {"type": "fixed", "value": -10},
                    "2027-W02": {"type": "fixed", "value": 20},
                    "2027-W03": {"type": "fixed", "value": 1},
                },
            }
        ],
        threshold=0,
        seed=1,
        n_simulations=1_000,
    )
    assert [p.p_below_threshold for p in out.result.by_period] == [1.0, 0.0, 0.0]
    assert [p.expected_cumulative for p in out.result.by_period] == [-5.0, 15.0, 16.0]
    assert out.result.horizon.p_ever_below_threshold == 1.0
    assert out.result.horizon.cfar == 0.0
    assert [c.share for c in out.result.contributions] == [0.0]


def test_missing_period_counts_as_zero() -> None:
    out = simulate_aggregate(
        periods=P2,
        components=[component("a", 1, {"type": "fixed", "value": 3}, periods=["2027-02"])],
        seed=1,
        n_simulations=1_000,
    )
    assert [p.expected_flow for p in out.result.by_period] == [0.0, 3.0]


def test_same_group_is_comonotonic() -> None:
    out = simulate_aggregate(
        periods=["2027-01"],
        components=[
            component("a", 1, normal(0, 1), ["2027-01"], correlation_group="g"),
            component("b", 1, normal(0, 1), ["2027-01"], correlation_group="g"),
        ],
        seed=1,
        n_simulations=100_000,
    )
    q95 = out.result.by_period[0].flow_quantiles["0.95"]
    assert q95 == pytest.approx(2 * float(special.ndtri(0.95)), abs=0.03)


def test_rank_correlation_between_groups() -> None:
    rho = 0.6
    out = simulate_aggregate(
        periods=["2027-01"],
        components=[
            component("a", 1, normal(0, 1), ["2027-01"], correlation_group="ca"),
            component("b", 1, normal(0, 1), ["2027-01"], correlation_group="achats"),
        ],
        correlation={"variables": ["ca", "achats"], "matrix": [[1, rho], [rho, 1]]},
        seed=1,
        n_simulations=100_000,
    )
    r = 2 * math.sin(math.pi * rho / 6)  # Correlation of the underlying normal variables
    q95 = out.result.by_period[0].flow_quantiles["0.95"]
    assert q95 == pytest.approx(float(special.ndtri(0.95)) * math.sqrt(2 + 2 * r), abs=0.03)
    assert out.assumptions["correlation"] == {"variables": ["ca", "achats"], "matrix": [[1.0, 0.6], [0.6, 1.0]]}
    assert WarningCode.CORRELATION_ADJUSTED not in [w.code for w in out.warnings]


def test_slightly_invalid_correlation_is_adjusted_with_warning() -> None:
    groups = ["x", "y", "z"]
    out = simulate_aggregate(
        periods=["2027-01"],
        components=[component(g, 1, normal(100, 1), ["2027-01"], correlation_group=g) for g in groups],
        correlation={"variables": groups, "matrix": [[1, 0.8, 0.8], [0.8, 1, 0.25], [0.8, 0.25, 1]]},
        seed=1,
        n_simulations=1_000,
    )
    assert [w.code for w in out.warnings] == [WarningCode.CORRELATION_ADJUSTED]
    assert out.assumptions["correlation"]["matrix"][1][2] != 0.25


def test_non_positive_definite_correlation_is_impossible() -> None:
    groups = ["x", "y", "z"]
    with pytest.raises(ComputationImpossible):
        simulate_aggregate(
            periods=["2027-01"],
            components=[component(g, 1, normal(0, 1), ["2027-01"], correlation_group=g) for g in groups],
            correlation={"variables": groups, "matrix": [[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]]},
            n_simulations=1_000,
        )


def test_high_uncertainty_warning() -> None:
    out = simulate_aggregate(
        periods=["2027-01"],
        components=[component("a", 1, normal(10, 100), ["2027-01"])],
        seed=1,
        n_simulations=1_000,
    )
    assert [w.code for w in out.warnings] == [WarningCode.HIGH_UNCERTAINTY]


def test_assumptions_include_defaults() -> None:
    a = simulate_aggregate(two_normals(n_simulations=1_000)).assumptions
    assert a["quantiles"] == [0.05, 0.10, 0.50, 0.90, 0.95]
    assert a["confidence"] == 0.95
    assert a["opening_balance"] == 0.0
    assert a["threshold"] is None
    assert a["random_generator"] == "PCG64"


def test_probabilities_stay_in_unit_interval() -> None:
    out = simulate_aggregate(two_normals(threshold=100, n_simulations=1_000))
    for p in out.result.by_period:
        assert p.p_below_threshold is not None and 0 <= p.p_below_threshold <= 1


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"periods": ["2027-02", "2027-01"]}, ("periods", 1)),
        ({"periods": ["2027-01", "2027-W05"]}, ("periods",)),
        ({"periods": ["2027-13"]}, ("periods", 0)),
        ({"n_simulations": 500}, ("n_simulations",)),
        ({"quantiles": [0.5, 1.0]}, ("quantiles", 1)),
    ],
)
def test_invalid_input_paths(change: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        simulate_aggregate(two_normals(**change))
    assert path in [e.path for e in exc.value.errors]


def test_unknown_period_in_component() -> None:
    req = two_normals()
    req["components"][0] = component("in", 1, normal(1, 1), ["2027-01", "2027-03"])
    with pytest.raises(InvalidInput) as exc:
        simulate_aggregate(req)
    assert exc.value.errors[0].path == ("components", 0, "by_period", "2027-03")


def test_duplicate_component_and_unknown_correlation_variable() -> None:
    req = two_normals(correlation={"variables": ["a", "b"], "matrix": [[1, 0], [0, 1]]})
    req["components"][1]["id"] = "in"
    with pytest.raises(InvalidInput) as exc:
        simulate_aggregate(req)
    paths = [e.path for e in exc.value.errors]
    assert ("components", 1, "id") in paths
    assert ("correlation", "variables", 0) in paths


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"n_simulations": 100_001}, "n_simulations"),
        ({"periods": [f"{2027 + i // 12}-{i % 12 + 1:02d}" for i in range(61)], "components": []}, "periods"),
    ],
)
def test_limits(change: dict[str, Any], field: str) -> None:
    req = two_normals(**change)
    if not req["components"]:
        req["components"] = [component("a", 1, normal(0, 1), ["2027-01"])]
    with pytest.raises(InputTooLarge) as exc:
        simulate_aggregate(req)
    assert exc.value.field == field


def test_adding_a_component_keeps_other_draws_unchanged() -> None:
    base = two_normals(n_simulations=25_000, quantiles=[0.05, 0.5, 0.95])
    extra = {**base, "components": [*base["components"], component("bonus", 1, {"type": "fixed", "value": 7})]}
    before, after = simulate_aggregate(base).result, simulate_aggregate(extra).result
    for b, a in zip(before.by_period, after.by_period, strict=True):
        assert a.flow_quantiles == {k: pytest.approx(v + 7, abs=0.006) for k, v in b.flow_quantiles.items()}


def test_adding_a_correlation_group_keeps_existing_groups_unchanged() -> None:
    grouped = [component(g, 1, normal(0, 1), correlation_group=g) for g in ("x", "y")]
    base = {"periods": P2, "components": grouped, "seed": 3, "n_simulations": 1_000}
    corr = {"variables": ["x", "y"], "matrix": [[1, 0.5], [0.5, 1]]}
    a = simulate_aggregate({**base, "correlation": corr}).result
    third = component("z", 1, {"type": "fixed", "value": 0}, correlation_group="z")
    b = simulate_aggregate({**base, "components": [*grouped, third], "correlation": corr}).result
    assert a.by_period == b.by_period


def test_persistent_component_draws_once_for_the_horizon() -> None:
    hire = {"type": "bernoulli", "p": 0.5, "value": 100}
    periods = ["2027-01", "2027-02", "2027-03"]
    base = {"periods": periods, "seed": 4, "quantiles": [0.25, 0.75]}
    held = simulate_aggregate(
        {
            **base,
            "components": [{"id": "hire", "sign": 1, "persistent": True, "by_period": dict.fromkeys(periods, hire)}],
        }
    )
    end = held.result.by_period[-1].cumulative_quantiles
    assert (end["0.25"], end["0.75"]) == (0.0, 300.0)  # all three months or none
    fresh = simulate_aggregate(
        {**base, "components": [{"id": "hire", "sign": 1, "by_period": dict.fromkeys(periods, hire)}]}
    ).result.by_period[-1]
    assert (fresh.cumulative_quantiles["0.25"], fresh.cumulative_quantiles["0.75"]) == (100.0, 200.0)
    assert held.result.by_period[-1].expected_cumulative == pytest.approx(150, abs=3)


def test_cost_orientation_measures_the_upper_tail() -> None:
    cost = {"id": "cost", "sign": 1, "by_period": {"2027-01": {"type": "normal", "mean": 1000, "sd": 100}}}
    common = {"periods": ["2027-01"], "components": [cost], "seed": 1, "n_simulations": 20_000}
    high = simulate_aggregate({**common, "orientation": "lower_is_better"})
    low = simulate_aggregate(common)
    z95 = float(special.ndtri(0.95))
    assert high.result.horizon.cfar == pytest.approx(100 * z95, rel=0.03)  # q95 − mean
    assert high.result.horizon.expected_shortfall > 1000 > low.result.horizon.expected_shortfall
    assert high.assumptions["orientation"] == "lower_is_better"


def test_persistent_group_draws_once_and_keeps_its_components_together() -> None:
    periods = ["2027-W01", "2027-W02"]
    on_time = {
        "id": "on_time",
        "sign": 1,
        "persistent": True,
        "correlation_group": "invoice",
        "by_period": {
            "2027-W01": {"type": "bernoulli", "p": 0.6, "value": 100},
            "2027-W02": {"type": "bernoulli", "p": 0.6, "value": -100},
        },
    }
    late = {
        "id": "late",
        "sign": 1,
        "persistent": True,
        "correlation_group": "invoice",
        "by_period": {"2027-W02": {"type": "bernoulli", "p": 0.9, "value": 100}},
    }
    out = simulate_aggregate(periods=periods, components=[on_time, late], seed=3, quantiles=[0.05, 0.5])
    end = out.result.by_period[-1]
    # Paid once at most: on time (60%), late (30%), or not within the horizon (10%).
    assert end.cumulative_quantiles == {"0.05": 0.0, "0.5": 100.0}
    assert end.expected_cumulative == pytest.approx(90, abs=2)
    assert out.result.by_period[0].expected_flow == pytest.approx(60, abs=2)


def test_correlation_group_cannot_mix_persistent_components() -> None:
    held = {
        "id": "a",
        "sign": 1,
        "persistent": True,
        "correlation_group": "g",
        "by_period": {"2027-01": {"type": "fixed", "value": 1}},
    }
    fresh = {"id": "b", "sign": 1, "correlation_group": "g", "by_period": {"2027-01": {"type": "fixed", "value": 1}}}
    with pytest.raises(InvalidInput) as exc:
        simulate_aggregate(periods=["2027-01"], components=[held, fresh])
    assert exc.value.errors[0].path == ("components", 0, "persistent")


def test_persistent_groups_are_correlated_and_stay_constant() -> None:
    def event(name: str) -> dict[str, Any]:
        return {
            "id": name,
            "sign": 1,
            "persistent": True,
            "correlation_group": name,
            "by_period": {p: {"type": "bernoulli", "p": 0.5, "value": 1} for p in ("2027-01", "2027-02")},
        }

    noise = {
        "id": "noise",
        "sign": 1,
        "correlation_group": "noise",
        "by_period": {"2027-01": {"type": "normal", "mean": 0, "sd": 0.001}},
    }
    corr = {"variables": ["a", "b", "noise"], "matrix": [[1, 0.99, 0.3], [0.99, 1, 0.3], [0.3, 0.3, 1]]}
    out = simulate_aggregate(
        periods=["2027-01", "2027-02"],
        components=[noise, event("a"), event("b")],
        correlation=corr,
        seed=2,
        quantiles=[0.25, 0.75],
    )
    end = out.result.by_period[-1].cumulative_quantiles
    assert (round(end["0.25"]), round(end["0.75"])) == (0, 4)  # both events, in both months, or none
