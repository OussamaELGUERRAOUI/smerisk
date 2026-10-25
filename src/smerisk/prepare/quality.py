"""data_quality report (§11.4)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from smerisk.contracts import MAX_IGNORED_LISTED, Parsed
from smerisk.prepare.series import SeriesFit, missing_months


class Coverage(BaseModel):
    first: str
    last: str


class QualityOutlier(BaseModel):
    source: str
    """The series or the contract where the value was found."""
    ref: str
    """Period or identifier."""
    value: float
    deviation: float
    """In median absolute deviations."""


class Requirement(BaseModel):
    parameter: str
    minimum: int
    found: int
    met: bool


class IgnoredEntry(BaseModel):
    contract: str
    index: int
    field: str | None
    reason: str


class DataQuality(BaseModel):
    coverage: dict[str, Coverage]
    """First and last period found, by data type."""
    missing_periods: dict[str, list[str]]
    """Expected months without data, by series; series without a gap are omitted."""
    outliers: list[QualityOutlier]
    requirements: list[Requirement]
    ignored_records: list[IgnoredEntry]
    """At most 100 listed, all contracts together."""
    ignored_counts: dict[str, int]
    """Ignored records by contract, listed or not."""
    usable: bool
    """``False`` when a minimum is not met: the result is then indicative only."""


class QualityReport:
    """Collect quality information while the data is prepared, then ``build()`` the report."""

    def __init__(self) -> None:
        self._coverage: dict[str, Coverage] = {}
        self._missing: dict[str, list[str]] = {}
        self._outliers: list[QualityOutlier] = []
        self._requirements: list[Requirement] = []
        self._ignored: list[IgnoredEntry] = []
        self._ignored_counts: dict[str, int] = {}

    def records(self, contract: str, parsed: Parsed[Any], periods: Iterable[str] = ()) -> None:
        """Ignored records of a contract, and its coverage from the periods (or ISO dates) of the kept records."""
        self.coverage(contract, periods)
        if parsed.n_ignored:
            self._ignored_counts[contract] = self._ignored_counts.get(contract, 0) + parsed.n_ignored
        for ignored in parsed.ignored:
            if len(self._ignored) < MAX_IGNORED_LISTED:
                self._ignored.append(IgnoredEntry(contract=contract, **ignored.model_dump()))

    def coverage(self, name: str, periods: Iterable[str]) -> None:
        """Periods are ``YYYY-MM`` months or ISO dates, so that string order is time order."""
        found = sorted(set(periods))
        if found:
            self._coverage[name] = Coverage(first=found[0], last=found[-1])

    def series(self, name: str, series: dict[str, float], fit: SeriesFit | None, first: str, last: str) -> None:
        """A monthly series expected from ``first`` to ``last``: coverage, missing months, and outliers."""
        self.coverage(name, series)
        gaps = missing_months(series, first, last)
        if gaps:
            self._missing[name] = gaps
        if fit is not None:
            self._outliers.extend(
                QualityOutlier(source=name, ref=o.period, value=o.value, deviation=o.deviation) for o in fit.outliers
            )

    def outlier(self, source: str, ref: str, value: float, deviation: float) -> None:
        self._outliers.append(QualityOutlier(source=source, ref=ref, value=value, deviation=deviation))

    def require(self, parameter: str, minimum: int, found: int) -> bool:
        """Record a minimum; returns whether it is met."""
        met = found >= minimum
        self._requirements.append(Requirement(parameter=parameter, minimum=minimum, found=found, met=met))
        return met

    def build(self) -> DataQuality:
        return DataQuality(
            coverage=self._coverage,
            missing_periods=self._missing,
            outliers=self._outliers,
            requirements=self._requirements,
            ignored_records=self._ignored,
            ignored_counts=self._ignored_counts,
            usable=all(r.met for r in self._requirements),
        )
