"""The fictitious data sets of ``examples/`` give their expected results (contract tests for applications, §9)."""

import json
import math
from pathlib import Path
from typing import Any

import pytest

import smerisk
from smerisk import usecases

EXAMPLES = sorted(p for p in (Path(__file__).resolve().parents[2] / "examples").iterdir() if p.is_dir())


def assert_close(actual: Any, expected: Any, path: str = "") -> None:
    """Equal structures; numbers equal to 1e-9 relative (platform rounding)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key in expected:
            assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), f"{path}: {actual} != {expected}"
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def test_every_use_case_has_an_example() -> None:
    assert sorted(p.name for p in EXAMPLES) == sorted(usecases.__all__)


@pytest.mark.parametrize("folder", EXAMPLES, ids=lambda p: p.name)
def test_example_gives_its_expected_result(folder: Path) -> None:
    inp = json.loads((folder / "input.json").read_text("utf-8"))
    expected = json.loads((folder / "expected.json").read_text("utf-8"))
    out = getattr(usecases, folder.name)(inp).model_dump(mode="json")
    out["meta"].pop("duration_ms")
    assert out["meta"].pop("library_version") == smerisk.__version__  # not in the files: it changes at each release
    assert_close(out, expected)
