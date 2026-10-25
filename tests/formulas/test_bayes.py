from typing import Any

import numpy as np
import pytest
from scipy import stats

from smerisk import ComputationImpossible, InvalidInput, beta_binomial, credibility
from smerisk.core.warnings import WarningCode


def group(id: str, *values: float, weight: float = 1.0) -> dict[str, Any]:
    return {"id": id, "observations": [{"value": v, "weight": weight} for v in values]}


# --- credibility ----------------------------------------------------------------------------------------------


def test_estimate_mode_by_hand() -> None:
    # A = (1, 3), B = (5, 7), unit weights: v = 4 / 2 = 2; overall mean 4; a = (16 − 2) / (4 − 8 / 4) = 7.
    out = credibility(mode="estimate", groups=[group("A", 1, 3), group("B", 5, 7)])
    p = out.result.parameters
    assert (p.v, p.a, p.k, p.mu) == pytest.approx((2, 7, 2 / 7, 4))
    a, b = out.result.groups
    assert a.credibility == pytest.approx(2 / (2 + 2 / 7))  # 7/8
    assert (a.observed_mean, a.estimate) == pytest.approx((2, 0.875 * 2 + 0.125 * 4))
    assert (b.observed_mean, b.estimate) == pytest.approx((6, 0.875 * 6 + 0.125 * 4))
    assert [w.code for w in out.warnings] == [WarningCode.SMALL_SAMPLE]


def test_weights_follow_exposure() -> None:
    groups = [
        {"id": "A", "observations": [{"value": 0.10, "weight": 30}, {"value": 0.30, "weight": 10}]},
        group("B", 0.05, 0.07, 0.06, weight=50),
        group("C", 0.20, 0.10, weight=5),
    ]
    out = credibility(mode="estimate", groups=groups, min_groups=3).result
    x = [np.array([o["value"] for o in g["observations"]]) for g in groups]
    w = [np.array([o["weight"] for o in g["observations"]]) for g in groups]
    wi = np.array([wi.sum() for wi in w])
    xi = np.array([(a * b).sum() / b.sum() for a, b in zip(x, w, strict=True)])
    v = sum(float((b * (a - m) ** 2).sum()) for a, b, m in zip(x, w, xi, strict=True)) / (1 + 2 + 1)
    xbar = (wi * xi).sum() / wi.sum()
    a_hat = ((wi * (xi - xbar) ** 2).sum() - 2 * v) / (wi.sum() - (wi**2).sum() / wi.sum())
    z = wi / (wi + v / a_hat)
    assert out.parameters.v == pytest.approx(v)
    assert out.parameters.a == pytest.approx(a_hat)
    assert [g.credibility for g in out.groups] == pytest.approx(list(z))
    assert out.parameters.mu == pytest.approx((z * xi).sum() / z.sum())
    # More exposure, more credibility.
    assert out.groups[1].credibility > out.groups[0].credibility > out.groups[2].credibility


def test_no_between_group_variance() -> None:
    out = credibility(mode="estimate", groups=[group("A", 1, 3), group("B", 1, 3)], min_groups=2)
    assert out.result.parameters.a == 0.0 and out.result.parameters.k is None
    assert [g.credibility for g in out.result.groups] == [0.0, 0.0]
    assert [g.estimate for g in out.result.groups] == [2.0, 2.0]
    assert [w.code for w in out.warnings] == [WarningCode.NO_BETWEEN_GROUP_VARIANCE]


def test_reference_mode_by_hand() -> None:
    out = credibility(
        mode="reference",
        groups=[group("restaurant", 0.08, 0.12, weight=4)],
        reference_mean=0.05,
        credibility_constant=8,
    )
    g = out.result.groups[0]
    assert (g.exposure, g.observed_mean, g.credibility) == pytest.approx((8, 0.1, 0.5))
    assert g.estimate == pytest.approx(0.075)
    assert out.result.parameters.model_dump() == {"mu": 0.05, "v": None, "a": None, "k": 8}
    assert out.warnings == []


def test_estimated_k_is_reusable_in_reference_mode() -> None:
    groups = [group("A", 1, 3), group("B", 5, 7)]
    est = credibility(mode="estimate", groups=groups).result
    ref = credibility(
        mode="reference", groups=groups, reference_mean=est.parameters.mu, credibility_constant=est.parameters.k
    ).result
    assert [g.estimate for g in ref.groups] == pytest.approx([g.estimate for g in est.groups])


def test_single_observation_groups_are_impossible() -> None:
    with pytest.raises(ComputationImpossible) as exc:
        credibility(mode="estimate", groups=[group("A", 1), group("B", 2)])
    assert exc.value.code == "WITHIN_VARIANCE_NOT_ESTIMABLE"


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"mode": "reference", "groups": [group("A", 1)], "reference_mean": 0.1}, ()),
        ({"mode": "estimate", "groups": [group("A", 1, 2)]}, ()),
        ({"mode": "estimate", "groups": [group("A", 1, 2), group("A", 3, 4)]}, ("groups", 1, "id")),
        (
            {"mode": "estimate", "groups": [group("A", 1, 2, weight=0), group("B", 3, 4)]},
            ("groups", 0, "observations", 0, "weight"),
        ),
        (
            {"mode": "estimate", "groups": [group("A", 1, 2), {"id": "B", "observations": []}]},
            ("groups", 1, "observations"),
        ),
        ({"mode": "other", "groups": [group("A", 1)]}, ("mode",)),
    ],
)
def test_credibility_invalid_input(fields: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        credibility(fields)
    assert path in [e.path for e in exc.value.errors]


# --- beta_binomial --------------------------------------------------------------------------------------------


def test_mean_strength_prior_by_hand() -> None:
    out = beta_binomial(successes=8, trials=10, prior={"mean": 0.6, "strength": 5})
    r = out.result
    assert (r.posterior.alpha, r.posterior.beta) == pytest.approx((3 + 8, 2 + 2))
    assert r.mean == pytest.approx(11 / 15)
    assert r.observed_rate == 0.8
    assert r.prior_weight == pytest.approx(5 / 15)
    assert r.credible_interval.lower == pytest.approx(stats.beta.ppf(0.05, 11, 4))
    assert r.credible_interval.upper == pytest.approx(stats.beta.ppf(0.95, 11, 4))
    assert out.assumptions["prior"] == {"alpha": pytest.approx(3), "beta": pytest.approx(2), "source": "mean_strength"}
    assert out.warnings == []


def test_alpha_beta_prior_and_level() -> None:
    r = beta_binomial(successes=2, trials=3, prior={"alpha": 2, "beta": 8}, credible_level=0.5).result
    assert (r.posterior.alpha, r.posterior.beta) == (4, 9)
    assert r.credible_interval.lower == pytest.approx(stats.beta.ppf(0.25, 4, 9))


def test_default_prior_warns() -> None:
    out = beta_binomial(successes=0, trials=4)
    assert (out.result.posterior.alpha, out.result.posterior.beta) == (1, 5)
    assert [w.code for w in out.warnings] == [WarningCode.DEFAULT_PRIOR]
    assert out.assumptions["prior"]["source"] == "default"


def test_no_trials_returns_the_prior() -> None:
    r = beta_binomial(successes=0, trials=0, prior={"mean": 0.25, "strength": 4}).result
    assert r.mean == pytest.approx(0.25)
    assert r.observed_rate is None and r.prior_weight == 1.0


def test_prior_weight_decreases_with_data() -> None:
    weights = [
        beta_binomial(successes=n // 2, trials=n, prior={"alpha": 1, "beta": 1}).result.prior_weight
        for n in (0, 2, 10, 100)
    ]
    assert weights == sorted(weights, reverse=True)


def test_posterior_feeds_utilization_forecast() -> None:
    from smerisk import utilization_forecast

    posterior = beta_binomial(successes=6, trials=8, prior={"mean": 0.6, "strength": 5}).result.posterior
    utilization_forecast(
        periods=["2027-01", "2027-02"],
        consultants=[
            {
                "id": "c",
                "available_days": {"2027-01": 20, "2027-02": 20},
                "current_mission": {
                    "end_period": "2027-01",
                    "billable_days_per_period": 20,
                    "renewal_probability_beta": posterior.model_dump(),
                    "renewal_duration_months": {"type": "fixed", "value": 3},
                },
            }
        ],
        n_simulations=1_000,
    )


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"successes": 5, "trials": 4}, ()),
        ({"successes": -1, "trials": 4}, ("successes",)),
        ({"successes": 1, "trials": 4, "prior": {"mean": 1.0, "strength": 3}}, ("prior",)),
        ({"successes": 1, "trials": 4, "prior": {"alpha": 0, "beta": 1}}, ("prior",)),
        ({"successes": 1, "trials": 4, "credible_level": 1}, ("credible_level",)),
    ],
)
def test_beta_binomial_invalid_input(fields: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        beta_binomial(fields)
    assert any(e.path[: len(path)] == path for e in exc.value.errors)
