"""forecast-reliability: Brier score of past probabilities and Kupiec test of past intervals (§12)."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from smerisk.contracts import ForecastLogEntry, parse_records
from smerisk.core.envelope import InputModel, Warning, parse_input
from smerisk.formulas.validation import MIN_OBSERVATIONS as KUPIEC_MIN_OBSERVATIONS
from smerisk.formulas.validation import BrierResult, KupiecResult, brier, kupiec
from smerisk.prepare import DerivedParameters, Estimate, QualityReport
from smerisk.usecases._base import Records, Settings, UsecaseResult, require, respond

MIN_PROBABILITIES = 30


class ForecastReliabilitySettings(Settings):
    n_bins: int = Field(10, ge=1, le=100)
    """Calibration bins of the Brier score."""


class ForecastReliabilityInput(InputModel):
    forecast_log: Records = Field(default_factory=list)
    settings: ForecastReliabilitySettings = Field(default_factory=ForecastReliabilitySettings)


class IntervalTest(BaseModel):
    level: float
    exceedances: int
    observations: int
    test: KupiecResult


class ReliabilityResult(BaseModel):
    probabilities: BrierResult | None
    """``None`` without a resolved probability forecast."""
    intervals: list[IntervalTest]
    """One Kupiec test per interval level."""


def forecast_reliability(
    params: ForecastReliabilityInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[ReliabilityResult]:
    started = time.perf_counter()
    inp = parse_input(ForecastReliabilityInput, params, fields)
    settings = inp.settings
    parsed = parse_records(ForecastLogEntry, inp.forecast_log, name="forecast_log")
    records = [r for r in parsed.records if r.made_at.date() <= settings.as_of]
    quality = QualityReport()
    quality.records("forecast_log", parsed, (r.made_at.date().isoformat() for r in records))

    probabilities = [r for r in records if r.kind == "PROBABILITY" and r.outcome is not None]
    by_level: dict[float, list[ForecastLogEntry]] = defaultdict(list)
    for r in records:
        if r.exceeded is not None and r.level is not None:
            by_level[r.level].append(r)
    require("forecast_log", len(probabilities) + len(by_level), "no forecast with a known outcome")

    derived = DerivedParameters()
    if probabilities:
        quality.require("forecast_log.probabilities", MIN_PROBABILITIES, len(probabilities))
        dates = sorted(r.made_at.date().isoformat() for r in probabilities)
        derived.add(
            "probabilities.forecasts",
            Estimate(
                value=[{"p": r.p, "outcome": int(r.outcome or 0)} for r in probabilities],
                source="estimated",
                method="PROBABILITY forecasts with a known outcome",
                n_points=len(probabilities),
                first=dates[0],
                last=dates[-1],
            ),
            list[dict[str, Any]],
        )
    for level, entries in sorted(by_level.items()):
        quality.require(f"forecast_log.intervals.{level:g}", KUPIEC_MIN_OBSERVATIONS, len(entries))
        derived.add(
            f"intervals.{level:g}.exceedances",
            Estimate(
                value=sum(bool(r.exceeded) for r in entries),
                source="estimated",
                method="actual value outside the interval",
                n_points=len(entries),
            ),
        )
    derived.apply(settings.overrides)

    warnings: list[Warning] = []
    assumptions: dict[str, Any] = {}
    probability_result = None
    if probabilities:
        scored = brier(forecasts=derived.value("probabilities.forecasts"), n_bins=settings.n_bins)
        probability_result = scored.result
        warnings.extend(scored.warnings)
        assumptions["brier"] = scored.assumptions
    intervals = []
    for level, entries in sorted(by_level.items()):
        exceedances = derived.value(f"intervals.{level:g}.exceedances")
        tested = kupiec(exceedances=exceedances, observations=len(entries), expected_rate=round(1 - level, 12))
        intervals.append(
            IntervalTest(level=level, exceedances=exceedances, observations=len(entries), test=tested.result)
        )
        warnings.extend(Warning(code=w.code, message=f"intervals at {level:g}: {w.message}") for w in tested.warnings)
        assumptions["kupiec"] = tested.assumptions
    return respond(
        ReliabilityResult(probabilities=probability_result, intervals=intervals),
        method="forecast-reliability",
        started=started,
        settings=settings,
        assumptions=assumptions,
        warnings=warnings,
        derived=derived,
        quality=quality,
    )
