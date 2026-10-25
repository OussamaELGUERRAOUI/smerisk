"""risk_measures: VaR, Expected Shortfall, CFaR (§3.2)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from smerisk.core.envelope import InputModel, Result, envelope, parse_input
from smerisk.core.errors import MAX_OBSERVATIONS, check_limit
from smerisk.core.types import Level

Orientation = Literal["higher_is_better", "lower_is_better"]

MIN_SAMPLES = 100


class RiskMeasuresInput(InputModel):
    samples: list[float] = Field(min_length=MIN_SAMPLES)
    confidence: Level = 0.95
    orientation: Orientation = "higher_is_better"


class RiskMeasures(BaseModel):
    quantile: float
    var: float
    expected_shortfall: float
    mean: float
    sd: float


def compute_risk_measures(x: NDArray[np.float64], confidence: float, orientation: Orientation) -> RiskMeasures:
    """Compute measures on an array without validation; also used by other formulas."""
    mean = float(x.mean())
    if orientation == "higher_is_better":
        q = float(np.quantile(x, 1 - confidence))
        var, tail = mean - q, x[x <= q]
    else:
        q = float(np.quantile(x, confidence))
        var, tail = q - mean, x[x >= q]
    return RiskMeasures(
        quantile=q,
        var=var,
        expected_shortfall=float(tail.mean()),
        mean=mean,
        sd=float(x.std(ddof=1)),
    )


def risk_measures(
    params: RiskMeasuresInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[RiskMeasures]:
    started = time.perf_counter()
    inp = parse_input(RiskMeasuresInput, params, fields)
    check_limit("samples", len(inp.samples), MAX_OBSERVATIONS)
    result = compute_risk_measures(np.asarray(inp.samples, dtype=np.float64), inp.confidence, inp.orientation)
    return envelope(
        result,
        assumptions={
            "confidence": inp.confidence,
            "orientation": inp.orientation,
            "quantile_method": "linear",
        },
        warnings=[],
        method="empirical-quantile",
        started=started,
    )
