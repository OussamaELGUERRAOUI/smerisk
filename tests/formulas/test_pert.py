import math
from typing import Any

import pytest
from scipy import special

from smerisk import InvalidInput, pert_estimate
from smerisk.core.warnings import WarningCode
from smerisk.formulas.pert import PertInput

SPEC = {"id": "spec", "optimistic": 2, "most_likely": 3, "pessimistic": 6}
DEV = {"id": "dev", "optimistic": 5, "most_likely": 8, "pessimistic": 15}


def test_single_task_by_hand() -> None:
    r = pert_estimate(tasks=[SPEC]).result
    assert r.tasks[0].expected == pytest.approx((2 + 4 * 3 + 6) / 6)
    assert r.tasks[0].sd == pytest.approx((6 - 2) / 6)
    assert r.total.expected == pytest.approx(r.tasks[0].expected)
    assert r.total.sd == pytest.approx(r.tasks[0].sd)


def test_two_tasks_by_hand() -> None:
    r = pert_estimate(tasks=[SPEC, DEV]).result
    sd = math.sqrt((4 / 6) ** 2 + (10 / 6) ** 2)
    assert r.total.expected == pytest.approx(20 / 6 + 52 / 6)  # 12 days
    assert r.total.sd == pytest.approx(sd)
    assert r.total.at_confidence["0.5"] == pytest.approx(12)
    assert r.total.at_confidence["0.8"] == pytest.approx(12 + 0.8416212 * sd)
    assert r.total.at_confidence["0.9"] == pytest.approx(12 + 1.2815516 * sd)
    assert r.contingency == pytest.approx(r.total.at_confidence["0.8"] - 12)


def test_custom_levels_are_sorted_and_contingency_stays_at_80() -> None:
    out = pert_estimate(tasks=[SPEC, DEV], confidence_levels=[0.95, 0.6, 0.95])
    assert list(out.result.total.at_confidence) == ["0.6", "0.95"]
    assert out.result.contingency == pytest.approx(float(special.ndtri(0.8)) * out.result.total.sd)
    assert out.assumptions["confidence_levels"] == [0.6, 0.95]


def test_effort_increases_with_confidence() -> None:
    levels = [0.1, 0.3, 0.5, 0.7, 0.9, 0.99]
    efforts = list(pert_estimate(tasks=[SPEC, DEV], confidence_levels=levels).result.total.at_confidence.values())
    assert efforts == sorted(efforts)


def test_symmetric_task_has_mode_as_expectation() -> None:
    r = pert_estimate(tasks=[{"id": "t", "optimistic": 4, "most_likely": 5, "pessimistic": 6}]).result
    assert r.tasks[0].expected == pytest.approx(5)


def test_small_sample_warning_below_five_tasks() -> None:
    four = [{**SPEC, "id": f"t{i}"} for i in range(4)]
    assert [w.code for w in pert_estimate(tasks=four).warnings] == [WarningCode.SMALL_SAMPLE]
    assert pert_estimate(tasks=[*four, {**SPEC, "id": "t4"}]).warnings == []


def test_envelope_and_input_model() -> None:
    out = pert_estimate(PertInput.model_validate({"tasks": [SPEC]}))
    assert out.meta.method == "pert"
    assert out.assumptions["confidence_levels"] == [0.5, 0.8, 0.9]
    assert out.assumptions["task_independence"] is True


@pytest.mark.parametrize(
    ("task", "path"),
    [
        ({"optimistic": -1, "most_likely": 1, "pessimistic": 2}, ("tasks", 0, "optimistic")),
        ({"optimistic": 3, "most_likely": 2, "pessimistic": 5}, ("tasks", 0)),
        ({"optimistic": 1, "most_likely": 6, "pessimistic": 5}, ("tasks", 0)),
        ({"optimistic": 2, "most_likely": 2, "pessimistic": 2}, ("tasks", 0)),
    ],
)
def test_invalid_task(task: dict[str, Any], path: tuple[Any, ...]) -> None:
    with pytest.raises(InvalidInput) as exc:
        pert_estimate(tasks=[{"id": "t", **task}])
    assert exc.value.errors[0].path == path


def test_duplicate_ids() -> None:
    with pytest.raises(InvalidInput) as exc:
        pert_estimate(tasks=[SPEC, DEV, {**DEV}])
    assert exc.value.errors[0].path == ("tasks", 2, "id")


@pytest.mark.parametrize("fields", [{"tasks": []}, {"tasks": [SPEC], "confidence_levels": [1.0]}])
def test_invalid_request(fields: dict[str, Any]) -> None:
    with pytest.raises(InvalidInput):
        pert_estimate(fields)
