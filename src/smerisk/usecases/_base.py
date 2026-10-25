"""Shared settings, response, and helpers of the business use cases (§12)."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterable, Sequence
from typing import Any, Generic, TypeVar

from pydantic import Field

from smerisk._version import __version__
from smerisk.core.envelope import InputModel, Meta, Result, Warning
from smerisk.core.errors import FieldError, InvalidInput
from smerisk.core.montecarlo import DEFAULT_QUANTILES
from smerisk.core.types import Level, month_from_index, month_index
from smerisk.prepare import DataQuality, DerivedParameters, Estimate, QualityReport

T = TypeVar("T")

MIN_GROUP_HEADCOUNT = 3
OTHERS = "others"
"""Group that receives the groups of fewer than 3 employees (§12.2)."""

Records = list[Any]
"""Raw records: dictionaries, or records already validated with the models of ``smerisk.contracts``."""


class Settings(InputModel):
    """Common settings (§12.2). Each use case extends them with its own settings and defaults (§12.3)."""

    as_of: dt.date = Field(default_factory=dt.date.today)
    """Analysis date: later data is ignored, so that a past analysis can be replayed."""
    seed: int | None = Field(None, ge=0)
    overrides: dict[str, Any] = Field(default_factory=dict)
    """Parameters imposed by path, reported with the source ``override`` (§11.5)."""


class SimulationSettings(Settings):
    """Settings of the use cases that simulate."""

    n_simulations: int = Field(10_000, ge=1_000)
    confidence: Level = 0.95
    quantiles: list[Level] = Field(default_factory=lambda: list(DEFAULT_QUANTILES), min_length=1)


class UsecaseResult(Result[T], Generic[T]):
    """The response envelope (§2.1) with the parameters passed to the formulas and the data quality report."""

    derived_parameters: dict[str, Estimate[Any]]
    data_quality: DataQuality


def respond(
    result: T,
    *,
    method: str,
    started: float,
    settings: Settings,
    assumptions: dict[str, Any],
    warnings: Iterable[Warning],
    derived: DerivedParameters,
    quality: QualityReport,
) -> UsecaseResult[T]:
    """Build the response: the use case's settings first in ``assumptions``, then what the formulas used."""
    return UsecaseResult(
        result=result,
        assumptions={**settings.model_dump(mode="json", exclude={"overrides"}), **assumptions},
        warnings=_unique(warnings),
        meta=Meta(
            library_version=__version__, method=method, duration_ms=round((time.perf_counter() - started) * 1000)
        ),
        derived_parameters=derived.to_dict(),
        data_quality=quality.build(),
    )


def _unique(warnings: Iterable[Warning]) -> list[Warning]:
    seen: set[tuple[str, str]] = set()
    out = []
    for w in warnings:
        if (w.code, w.message) not in seen:
            seen.add((w.code, w.message))
            out.append(w)
    return out


def formula_warnings(*results: Result[Any]) -> list[Warning]:
    return [w for r in results for w in r.warnings]


def require(name: str, found: int, message: str = "at least one valid record is required") -> None:
    """A required input that is empty after validation and filtering is a 422, not an obscure formula error."""
    if found == 0:
        raise InvalidInput([FieldError((name,), message)])


def one_of(a: tuple[str, Sequence[Any]], b: tuple[str, Sequence[Any]]) -> tuple[str, Sequence[Any]]:
    """The one of two alternative contracts that was sent; both is a 422."""
    if a[1] and b[1]:
        raise InvalidInput([FieldError((b[0],), f"send {a[0]} or {b[0]}, not both")])
    return a if a[1] or not b[1] else b


def month_of(date: dt.date) -> str:
    return f"{date.year:04d}-{date.month:02d}"


def add_months(month: str, n: int) -> str:
    return month_from_index(month_index(month) + n)


def history_start(as_of: dt.date, months: int) -> str:
    """First month of a history of ``months`` complete months before the month of ``as_of``."""
    return add_months(month_of(as_of), -months)


def merge_small_groups(headcounts: dict[str, float]) -> dict[str, str]:
    """Group name → reported name: groups of fewer than 3 employees are merged into ``others`` (§12.2)."""
    return {g: (g if n >= MIN_GROUP_HEADCOUNT else OTHERS) for g, n in headcounts.items()}
