"""pert_estimate: classical PERT three-point effort estimate (§4.1)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Self

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy import special

from smerisk.core.envelope import InputModel, Result, Warning, envelope, parse_input
from smerisk.core.errors import MAX_OBSERVATIONS, FieldError, InvalidInput, check_limit
from smerisk.core.types import Level
from smerisk.core.warnings import WarningCode

DEFAULT_CONFIDENCE_LEVELS = [0.5, 0.8, 0.9]
CONTINGENCY_LEVEL = 0.8
MIN_TASKS = 5
"""Below this, the normal approximation of the total is less reliable (``SMALL_SAMPLE``)."""


class Task(InputModel):
    id: str = Field(min_length=1)
    optimistic: float = Field(ge=0)
    most_likely: float
    pessimistic: float

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if not self.optimistic <= self.most_likely <= self.pessimistic or self.optimistic == self.pessimistic:
            raise ValueError("requires optimistic ≤ most_likely ≤ pessimistic and optimistic < pessimistic")
        return self


class PertInput(InputModel):
    tasks: list[Task] = Field(min_length=1)
    confidence_levels: list[Level] = Field(default_factory=lambda: list(DEFAULT_CONFIDENCE_LEVELS), min_length=1)


class TaskEstimate(BaseModel):
    id: str
    expected: float
    sd: float


class Total(BaseModel):
    expected: float
    sd: float
    at_confidence: dict[str, float]
    """Effort (days) with the given probability of not being exceeded, keyed by level."""


class PertResult(BaseModel):
    tasks: list[TaskEstimate]
    total: Total
    contingency: float
    """Effort at 80% minus expected effort: suggested provision for contingencies (days)."""


def pert_estimate(params: PertInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[PertResult]:
    started = time.perf_counter()
    inp = parse_input(PertInput, params, fields)
    check_limit("tasks", len(inp.tasks), MAX_OBSERVATIONS)
    _check_unique_ids(inp.tasks)

    a = np.array([t.optimistic for t in inp.tasks])
    m = np.array([t.most_likely for t in inp.tasks])
    b = np.array([t.pessimistic for t in inp.tasks])
    expected = (a + 4 * m + b) / 6
    sd = (b - a) / 6
    total_expected = float(expected.sum())
    total_sd = float(np.sqrt((sd**2).sum()))

    levels = sorted(set(inp.confidence_levels))
    z = {level: float(special.ndtri(level)) for level in [*levels, CONTINGENCY_LEVEL]}
    warnings: list[Warning] = []
    if len(inp.tasks) < MIN_TASKS:
        warnings.append(
            Warning(
                code=WarningCode.SMALL_SAMPLE,
                message=f"{len(inp.tasks)} tasks, recommended minimum {MIN_TASKS}: the normal approximation of the "
                "total is less reliable; fixed_price_completion simulates it instead",
            )
        )

    return envelope(
        PertResult(
            tasks=[
                TaskEstimate(id=t.id, expected=float(e), sd=float(s))
                for t, e, s in zip(inp.tasks, expected, sd, strict=True)
            ],
            total=Total(
                expected=total_expected,
                sd=total_sd,
                at_confidence={f"{level:g}": total_expected + z[level] * total_sd for level in levels},
            ),
            contingency=z[CONTINGENCY_LEVEL] * total_sd,
        ),
        assumptions={
            "confidence_levels": levels,
            "z_values": {f"{level:g}": value for level, value in z.items()},
            "task_independence": True,
            "total_distribution": "normal",
            "contingency_level": CONTINGENCY_LEVEL,
        },
        warnings=warnings,
        method="pert",
        started=started,
    )


def _check_unique_ids(tasks: list[Task]) -> None:
    seen: set[str] = set()
    errors = []
    for i, t in enumerate(tasks):
        if t.id in seen:
            errors.append(FieldError(("tasks", i, "id"), f"duplicate id: {t.id}"))
        seen.add(t.id)
    if errors:
        raise InvalidInput(errors)
