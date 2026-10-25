"""Response envelope: Result, Warning, Meta (§2.1), and input parsing."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from smerisk._version import __version__
from smerisk.core.errors import FieldError, InvalidInput
from smerisk.core.warnings import WarningCode

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


class InputModel(BaseModel):
    """Input base model: reject unknown fields, NaN, and infinities."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Warning(BaseModel):
    code: WarningCode
    message: str


class Meta(BaseModel):
    library_version: str
    method: str
    duration_ms: int


class Result(BaseModel, Generic[T]):
    result: T
    assumptions: dict[str, Any]
    warnings: list[Warning]
    meta: Meta


def parse_input(model: type[M], params: M | Mapping[str, Any] | None, fields: Mapping[str, Any]) -> M:
    """Accept an existing model, a dictionary, or keyword arguments."""
    if params is not None and fields:
        raise TypeError("pass either an input object or keyword arguments, not both")
    if isinstance(params, model):
        return params
    try:
        return model.model_validate(params if params is not None else fields)
    except ValidationError as exc:
        raise InvalidInput([FieldError(tuple(e["loc"]), e["msg"]) for e in exc.errors()]) from None


def envelope(
    result: T,
    *,
    assumptions: dict[str, Any],
    warnings: list[Warning],
    method: str,
    started: float,
) -> Result[T]:
    """``started``: value of ``time.perf_counter()`` at the start of the computation."""
    return Result[T](
        result=result,
        assumptions=assumptions,
        warnings=warnings,
        meta=Meta(
            library_version=__version__,
            method=method,
            duration_ms=round((time.perf_counter() - started) * 1000),
        ),
    )
