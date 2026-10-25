from typing import Any

import numpy as np
import pytest
from scipy import stats

from smerisk import InvalidInput, kaplan_meier
from smerisk.core.warnings import WarningCode

# Freireich et al. (1963) leukemia remission data, the classic Kaplan-Meier example.
MP_T = [6, 6, 6, 6, 7, 9, 10, 10, 11, 13, 16, 17, 19, 20, 22, 23, 25, 32, 32, 34, 35]
MP_E = [1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0]
PLACEBO_T = [1, 1, 2, 2, 3, 4, 4, 5, 5, 8, 8, 8, 8, 11, 11, 12, 12, 15, 17, 22, 23]


def freireich() -> list[dict[str, Any]]:
    return [{"duration": t, "event": bool(e), "group": "6-MP"} for t, e in zip(MP_T, MP_E, strict=True)] + [
        {"duration": t, "event": True, "group": "placebo"} for t in PLACEBO_T
    ]


def subjects(*pairs: tuple[float, bool], group: str | None = None) -> list[dict[str, Any]]:
    return [{"duration": t, "event": e, "group": group} for t, e in pairs]


def curve(result: Any, group: str) -> Any:
    return next(c for c in result.result.curves if c.group == group)


def test_freireich_matches_published_curve() -> None:
    mp = curve(kaplan_meier(subjects=freireich()), "6-MP")
    assert [s.t for s in mp.steps] == [6, 7, 10, 13, 16, 22, 23]
    assert [round(s.survival, 3) for s in mp.steps] == [0.857, 0.807, 0.753, 0.690, 0.627, 0.538, 0.448]
    assert [s.at_risk for s in mp.steps] == [21, 17, 15, 12, 11, 7, 6]
    assert [s.events for s in mp.steps] == [3, 1, 1, 1, 1, 1, 1]
    assert mp.median == 23


def test_confidence_limits_match_scipy_log_log() -> None:
    mp = curve(kaplan_meier(subjects=freireich(), time_points=[6, 10, 23, 30]), "6-MP")
    data = stats.CensoredData(
        uncensored=[t for t, e in zip(MP_T, MP_E, strict=True) if e],
        right=[t for t, e in zip(MP_T, MP_E, strict=True) if not e],
    )
    sf = stats.ecdf(data).sf
    ci = sf.confidence_interval(0.95, method="log-log")
    t = [6, 10, 23, 30]
    np.testing.assert_allclose([p.survival for p in mp.at_time_points], sf.evaluate(t))
    np.testing.assert_allclose([p.lower for p in mp.at_time_points], ci.low.evaluate(t))
    np.testing.assert_allclose([p.upper for p in mp.at_time_points], ci.high.evaluate(t))


def test_log_rank_matches_scipy_and_literature() -> None:
    lr = kaplan_meier(subjects=freireich()).result.log_rank
    assert lr is not None and lr.groups == ["6-MP", "placebo"] and lr.df == 1
    mp = stats.CensoredData(
        uncensored=[t for t, e in zip(MP_T, MP_E, strict=True) if e],
        right=[t for t, e in zip(MP_T, MP_E, strict=True) if not e],
    )
    reference = stats.logrank(mp, stats.CensoredData(uncensored=PLACEBO_T))
    assert lr.statistic == pytest.approx(reference.statistic**2)
    assert lr.p_value == pytest.approx(reference.pvalue)
    assert lr.statistic == pytest.approx(16.79, abs=0.01)


def test_hand_computed_small_sample() -> None:
    # Event at 2 (3 at risk), censored at 3, event at 5 (1 at risk): S = 2/3, then 0.
    out = kaplan_meier(subjects=subjects((2, True), (3, False), (5, True)), time_points=[1, 2, 4, 5])
    all_ = curve(out, "ALL")
    assert [(s.t, s.at_risk, s.events) for s in all_.steps] == [(2, 3, 1), (5, 1, 1)]
    assert [s.survival for s in all_.steps] == pytest.approx([2 / 3, 0])
    assert (all_.steps[1].lower, all_.steps[1].upper) == (None, None)
    assert [p.survival for p in all_.at_time_points] == pytest.approx([1, 2 / 3, 2 / 3, 0])
    assert all_.median == 5
    assert [w.code for w in out.warnings] == [WarningCode.SMALL_SAMPLE]


def test_censored_at_an_event_time_is_still_at_risk() -> None:
    all_ = curve(kaplan_meier(subjects=subjects((2, True), (2, False), (4, True))), "ALL")
    assert all_.steps[0].at_risk == 3
    assert all_.steps[0].survival == pytest.approx(2 / 3)


def test_no_events_means_full_survival() -> None:
    all_ = curve(kaplan_meier(subjects=subjects((5, False), (8, False)), time_points=[3]), "ALL")
    assert all_.steps == [] and all_.median is None
    assert all_.at_time_points[0].survival == 1.0


def test_time_point_beyond_observation_is_not_extrapolated() -> None:
    out = kaplan_meier(subjects=freireich(), time_points=[12, 40])
    assert curve(out, "ALL").at_time_points[1].survival is None
    assert WarningCode.EXTRAPOLATION in [w.code for w in out.warnings]


def test_small_groups_are_suppressed() -> None:
    data = freireich() + subjects((4, True), (9, False), group="tiny")
    out = kaplan_meier(subjects=data, min_group_size=3)
    assert [c.group for c in out.result.curves] == ["ALL", "6-MP", "placebo"]
    assert out.result.suppressed_groups == ["tiny"]
    assert curve(out, "ALL").subjects == 44
    assert out.result.log_rank is not None and out.result.log_rank.groups == ["6-MP", "placebo"]


def test_identical_groups_have_no_log_rank_difference() -> None:
    pairs = [(t, bool(e)) for t, e in zip(MP_T, MP_E, strict=True)]
    lr = kaplan_meier(subjects=subjects(*pairs, group="a") + subjects(*pairs, group="b")).result.log_rank
    assert lr is not None and lr.statistic == pytest.approx(0, abs=1e-12) and lr.p_value == pytest.approx(1)


def test_three_groups() -> None:
    third = subjects(*[(t + 3, True) for t in PLACEBO_T], group="other")
    lr = kaplan_meier(subjects=freireich() + third).result.log_rank
    assert lr is not None and lr.df == 2 and 0 <= lr.p_value <= 1


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"subjects": [{"duration": -1, "event": True}]}, ("subjects", 0, "duration")),
        ({"subjects": [{"duration": 1, "event": True, "group": "ALL"}]}, ("subjects", 0, "group")),
        ({"subjects": [{"duration": 1, "event": True}], "time_points": [-3]}, ("time_points", 0)),
        ({"subjects": [{"duration": 1, "event": True}], "confidence": 1.5}, ("confidence",)),
        ({"subjects": []}, ("subjects",)),
    ],
)
def test_invalid_input(fields: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        kaplan_meier(fields)
    assert path in [e.path for e in exc.value.errors]
