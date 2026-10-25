"""kaplan_meier: Kaplan-Meier survival with Greenwood variance and the log-rank test (§6.1).

At each time ``t_i`` with at least one event, ``d_i`` events and ``n_i`` subjects at risk just before:

    S(t)      = prod(1 - d_i / n_i) for t_i <= t
    Var(S(t)) = S(t)^2 * sum(d_i / (n_i (n_i - d_i))) for t_i <= t      (Greenwood)

Confidence limits use the log(-log) transform, S^exp(±z·se) with se = sqrt(sum(...)) / |ln S|, so they stay in [0, 1].
Subjects censored at an event time are counted at risk for that event.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field
from scipy import special, stats

from smerisk.core.envelope import InputModel, Result, Warning, envelope, parse_input
from smerisk.core.errors import MAX_OBSERVATIONS, FieldError, InvalidInput, check_limit
from smerisk.core.types import Level
from smerisk.core.warnings import WarningCode

FloatArray = NDArray[np.float64]

ALL = "ALL"
DEFAULT_TIME_POINTS = [3.0, 6.0, 12.0, 24.0, 36.0]
MIN_SUBJECTS = 20
MIN_EVENTS = 5


class Subject(InputModel):
    duration: float = Field(ge=0)
    """Months until the event, or until the analysis date if it has not occurred."""
    event: bool
    """``True``: the event occurred; ``False``: censored (still present)."""
    group: str | None = Field(None, min_length=1)


class KaplanMeierInput(InputModel):
    subjects: list[Subject] = Field(min_length=1)
    min_group_size: int = Field(3, ge=1)
    time_points: list[float] = Field(default_factory=lambda: list(DEFAULT_TIME_POINTS))
    confidence: Level = 0.95


class Step(BaseModel):
    t: float
    survival: float
    lower: float | None
    upper: float | None
    """Confidence limits; ``None`` once survival reaches 0 (infinite Greenwood variance)."""
    at_risk: int
    events: int


class PointEstimate(BaseModel):
    t: float
    survival: float | None
    """``None`` beyond the longest observed duration, where the estimator is not defined."""
    lower: float | None
    upper: float | None


class Curve(BaseModel):
    group: str
    subjects: int
    events: int
    steps: list[Step]
    at_time_points: list[PointEstimate]
    median: float | None
    """First time where survival is at most 0.5; ``None`` if never reached."""


class LogRank(BaseModel):
    groups: list[str]
    statistic: float
    df: int
    p_value: float


class KaplanMeierResult(BaseModel):
    curves: list[Curve]
    suppressed_groups: list[str]
    """Groups smaller than ``min_group_size``, not returned for confidentiality."""
    log_rank: LogRank | None


@dataclass(frozen=True)
class _Table:
    """Event times with at-risk counts and event counts."""

    times: FloatArray
    at_risk: NDArray[np.int64]
    events: NDArray[np.int64]


def kaplan_meier(
    params: KaplanMeierInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[KaplanMeierResult]:
    started = time.perf_counter()
    inp = parse_input(KaplanMeierInput, params, fields)
    check_limit("subjects", len(inp.subjects), MAX_OBSERVATIONS)
    _check(inp)

    duration = np.array([s.duration for s in inp.subjects])
    event = np.array([s.event for s in inp.subjects])
    labels = np.array([s.group or "" for s in inp.subjects])
    names = sorted({s.group for s in inp.subjects if s.group})
    kept = [g for g in names if (labels == g).sum() >= inp.min_group_size]
    z = float(special.ndtri(0.5 + inp.confidence / 2))
    time_points = sorted(set(inp.time_points))

    curves, warnings = [], []
    for name in [ALL, *kept]:
        mask = np.ones(len(duration), dtype=bool) if name == ALL else labels == name
        curve = _curve(name, duration[mask], event[mask], time_points, z)
        curves.append(curve)
        if curve.subjects < MIN_SUBJECTS or curve.events < MIN_EVENTS:
            warnings.append(
                Warning(
                    code=WarningCode.SMALL_SAMPLE,
                    message=f"{name}: {curve.subjects} subjects and {curve.events} events, recommended minimum "
                    f"{MIN_SUBJECTS} subjects and {MIN_EVENTS} events",
                )
            )
    beyond = [t for t in time_points if t > duration.max()]
    if beyond:
        warnings.append(
            Warning(
                code=WarningCode.EXTRAPOLATION,
                message=f"time points {beyond} are beyond the longest observed duration ({duration.max():g})",
            )
        )

    log_rank = None
    if len(kept) >= 2:
        masks = [labels == g for g in kept]
        statistic = _log_rank([duration[m] for m in masks], [event[m] for m in masks])
        log_rank = LogRank(
            groups=kept, statistic=statistic, df=len(kept) - 1, p_value=float(stats.chi2.sf(statistic, len(kept) - 1))
        )

    return envelope(
        KaplanMeierResult(curves=curves, suppressed_groups=[g for g in names if g not in kept], log_rank=log_rank),
        assumptions={
            "confidence": inp.confidence,
            "min_group_size": inp.min_group_size,
            "time_points": time_points,
            "variance": "greenwood",
            "confidence_interval": "log-log",
            "median_rule": "first time with survival <= 0.5",
            "ties": "censored subjects are at risk at their censoring time",
        },
        warnings=warnings,
        method="kaplan-meier",
        started=started,
    )


def _check(inp: KaplanMeierInput) -> None:
    errors = [
        FieldError(("subjects", i, "group"), f"{ALL} is reserved for the full sample")
        for i, s in enumerate(inp.subjects)
        if s.group == ALL
    ]
    errors += [
        FieldError(("time_points", i), "time points must be non-negative")
        for i, t in enumerate(inp.time_points)
        if t < 0
    ]
    if errors:
        raise InvalidInput(errors)


def _table(duration: FloatArray, event: NDArray[np.bool_]) -> _Table:
    times, events = np.unique(duration[event], return_counts=True)
    ordered = np.sort(duration)
    at_risk = len(duration) - np.searchsorted(ordered, times, side="left")
    return _Table(times, at_risk.astype(np.int64), events.astype(np.int64))


def _curve(name: str, duration: FloatArray, event: NDArray[np.bool_], time_points: list[float], z: float) -> Curve:
    table = _table(duration, event)
    n, d = table.at_risk.astype(np.float64), table.events.astype(np.float64)
    survival = np.cumprod(1 - d / n)
    with np.errstate(divide="ignore", invalid="ignore"):
        greenwood = np.cumsum(d / (n * (n - d)))
        se = np.sqrt(greenwood) / np.abs(np.log(survival))
        lower = survival ** np.exp(z * se)
        upper = survival ** np.exp(-z * se)
    defined = (survival > 0) & np.isfinite(se)

    def limits(i: int) -> tuple[float | None, float | None]:
        return (float(lower[i]), float(upper[i])) if defined[i] else (None, None)

    steps = [
        Step(
            t=float(t),
            survival=float(s),
            lower=limits(i)[0],
            upper=limits(i)[1],
            at_risk=int(table.at_risk[i]),
            events=int(table.events[i]),
        )
        for i, (t, s) in enumerate(zip(table.times, survival, strict=True))
    ]
    points = []
    for t in time_points:
        i = int(np.searchsorted(table.times, t, side="right")) - 1  # last event time <= t
        if t > duration.max():
            points.append(PointEstimate(t=t, survival=None, lower=None, upper=None))
        elif i < 0:
            points.append(PointEstimate(t=t, survival=1.0, lower=1.0, upper=1.0))
        else:
            points.append(PointEstimate(t=t, survival=float(survival[i]), lower=limits(i)[0], upper=limits(i)[1]))
    below = np.flatnonzero(survival <= 0.5)
    return Curve(
        group=name,
        subjects=len(duration),
        events=int(event.sum()),
        steps=steps,
        at_time_points=points,
        median=float(table.times[below[0]]) if below.size else None,
    )


def _log_rank(durations: list[FloatArray], events: list[NDArray[np.bool_]]) -> float:
    """Chi-square statistic of the log-rank test across groups (G - 1 degrees of freedom)."""
    times = np.unique(np.concatenate([d[e] for d, e in zip(durations, events, strict=True)]))
    # at_risk[g, i] and deaths[g, i] at each pooled event time.
    at_risk = np.array([len(d) - np.searchsorted(np.sort(d), times, side="left") for d in durations], dtype=np.float64)
    deaths = np.array(
        [
            np.searchsorted(np.sort(d[e]), times, side="right") - np.searchsorted(np.sort(d[e]), times, side="left")
            for d, e in zip(durations, events, strict=True)
        ],
        dtype=np.float64,
    )
    n, d = at_risk.sum(axis=0), deaths.sum(axis=0)
    observed_minus_expected = (deaths - at_risk * d / n).sum(axis=1)
    share = at_risk / n
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = np.where(n > 1, d * (n - d) / (n - 1), 0.0)
    # V[g, h] = sum_i factor_i * share_g,i * (delta_gh - share_h,i)
    covariance = np.einsum("i,gi,hi->gh", factor, share, -share) + np.diag((factor * share).sum(axis=1))
    u, v = observed_minus_expected[:-1], covariance[:-1, :-1]
    return float(u @ np.linalg.solve(v, u))
