"""brier and kupiec (§8.1, §8.2): does a forecast do better than a simple rule?"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal, Self

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy import special, stats

from smerisk.core.envelope import InputModel, Result, Warning, envelope, parse_input
from smerisk.core.errors import MAX_OBSERVATIONS, ComputationImpossible, check_limit
from smerisk.core.types import Rate
from smerisk.core.warnings import WarningCode

# --- §8.1 Brier score and calibration -------------------------------------------------------------------------


class Forecast(InputModel):
    p: Rate
    """Probability announced at the time."""
    outcome: Literal[0, 1]


class BrierInput(InputModel):
    forecasts: list[Forecast] = Field(min_length=1)
    baseline: Literal["climatology"] | Rate = "climatology"
    """Naive reference: the observed event rate (``climatology``) or a given rate."""
    n_bins: int = Field(10, ge=1, le=100)


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    mean_forecast: float
    observed_frequency: float
    count: int


class BrierResult(BaseModel):
    brier_score: float
    """From 0 (perfect) to 1; lower is better."""
    skill_score: float
    """1 − BS / BS_reference: above 0 the forecast beats the naive reference; at or below 0 it adds nothing."""
    baseline_rate: float
    reference_score: float
    calibration: list[CalibrationBin]
    """Non-empty bins of equal width on [0, 1]; a well-calibrated forecast lies near the diagonal."""


def brier(params: BrierInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[BrierResult]:
    started = time.perf_counter()
    inp = parse_input(BrierInput, params, fields)
    check_limit("forecasts", len(inp.forecasts), MAX_OBSERVATIONS)

    p = np.array([f.p for f in inp.forecasts])
    o = np.array([f.outcome for f in inp.forecasts], dtype=np.float64)
    rate = float(o.mean()) if inp.baseline == "climatology" else float(inp.baseline)
    score = float(((p - o) ** 2).mean())
    reference = float(((rate - o) ** 2).mean())
    if reference == 0:
        raise ComputationImpossible(
            "UNDEFINED_SKILL_SCORE",
            "the reference forecast is perfect (all outcomes equal the baseline rate): the skill score is undefined",
            {"brier_score": score},
        )

    edges = np.round(np.linspace(0, 1, inp.n_bins + 1), 12)  # 0.6, not 0.6000000000000001
    bins = np.minimum(np.searchsorted(edges, p, side="right") - 1, inp.n_bins - 1)  # p = 1 goes in the last bin
    calibration = [
        CalibrationBin(
            lower=float(edges[b]),
            upper=float(edges[b + 1]),
            mean_forecast=float(p[bins == b].mean()),
            observed_frequency=float(o[bins == b].mean()),
            count=int((bins == b).sum()),
        )
        for b in np.unique(bins)
    ]
    return envelope(
        BrierResult(
            brier_score=score,
            skill_score=1 - score / reference,
            baseline_rate=rate,
            reference_score=reference,
            calibration=calibration,
        ),
        assumptions={"baseline": inp.baseline, "n_bins": inp.n_bins, "bins": "equal width on [0, 1]"},
        warnings=[],
        method="brier",
        started=started,
    )


# --- §8.2 Kupiec proportion-of-failures test ------------------------------------------------------------------

SIGNIFICANCE = 0.05
MIN_OBSERVATIONS = 24


class KupiecInput(InputModel):
    exceedances: int = Field(ge=0)
    """x: periods where the actual value crossed the announced threshold."""
    observations: int = Field(ge=1)
    """T: periods tested."""
    expected_rate: float = Field(gt=0, lt=1)
    """p = 1 − confidence level, e.g. 0.05 for 95%."""

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if self.exceedances > self.observations:
            raise ValueError("exceedances cannot exceed observations")
        return self


class KupiecResult(BaseModel):
    observed_rate: float
    lr: float
    p_value: float
    verdict: Literal["OK", "TOO_MANY_EXCEEDANCES", "TOO_FEW_EXCEEDANCES"]
    """Too many: the risk is underestimated. Too few: the model is too cautious."""


def kupiec(params: KupiecInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[KupiecResult]:
    started = time.perf_counter()
    inp = parse_input(KupiecInput, params, fields)
    x, n, p = inp.exceedances, inp.observations, inp.expected_rate
    rate = x / n
    # xlogy applies the limit 0 · log(0) = 0 at x = 0 and x = T.
    lr = -2 * (special.xlogy(n - x, 1 - p) + special.xlogy(x, p)) + 2 * (
        special.xlogy(n - x, 1 - rate) + special.xlogy(x, rate)
    )
    lr = max(float(lr), 0.0)  # rounding can leave a tiny negative value when rate == p
    p_value = float(stats.chi2.sf(lr, 1))
    if p_value >= SIGNIFICANCE:
        verdict: Literal["OK", "TOO_MANY_EXCEEDANCES", "TOO_FEW_EXCEEDANCES"] = "OK"
    else:
        verdict = "TOO_MANY_EXCEEDANCES" if rate > p else "TOO_FEW_EXCEEDANCES"

    warnings = []
    if n < MIN_OBSERVATIONS:
        warnings.append(
            Warning(
                code=WarningCode.SMALL_SAMPLE,
                message=f"{n} periods, recommended minimum {MIN_OBSERVATIONS}: the test has little power",
            )
        )
    return envelope(
        KupiecResult(observed_rate=rate, lr=lr, p_value=p_value, verdict=verdict),
        assumptions={"significance": SIGNIFICANCE, "distribution": "chi-square, 1 degree of freedom"},
        warnings=warnings,
        method="kupiec-pof",
        started=started,
    )
