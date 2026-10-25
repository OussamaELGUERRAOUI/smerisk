"""Base record model and lenient parsing of raw data (§10, §11.4)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from smerisk.core.errors import check_limit

MAX_RECORDS = 1_000_000
"""Records per contract and per call. Raw data is aggregated before it reaches the formulas (§2.6 limits)."""

MAX_IGNORED_LISTED = 100
"""Ignored records listed in the quality report; the others are only counted (§11.4)."""

Ref = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
"""Opaque identifier (``*_ref``): never a name or any other personal data."""

Days = Annotated[float, Field(ge=0, le=31)]
"""Days in one month."""


class Record(BaseModel):
    """One raw record: unknown fields, NaN, and infinities are rejected; records are immutable."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


R = TypeVar("R", bound=Record)


class IgnoredRecord(BaseModel):
    index: int
    """Position of the record in the list sent (0 = first)."""
    field: str | None
    """Dotted path of the first invalid field, or ``None`` when the record as a whole is invalid."""
    reason: str


@dataclass
class Parsed(Generic[R]):
    records: list[R]
    ignored: list[IgnoredRecord] = field(default_factory=list)
    """At most ``MAX_IGNORED_LISTED`` entries."""
    n_ignored: int = 0


def _describe(exc: ValidationError) -> tuple[str | None, str]:
    errors = exc.errors(include_url=False)
    first = ".".join(map(str, errors[0]["loc"])) or None
    reason = "; ".join(f"{'.'.join(map(str, e['loc'])) or '<record>'}: {e['msg']}" for e in errors)
    return first, reason


def parse_records(model: type[R], rows: Iterable[Mapping[str, Any] | R], *, name: str) -> Parsed[R]:
    """Validate each record on its own: an invalid record is set aside with its reason instead of failing the call.

    ``name`` is the contract name, used in the size error (413).
    """
    rows = list(rows)
    check_limit(name, len(rows), MAX_RECORDS)
    parsed: Parsed[R] = Parsed(records=[])
    for index, row in enumerate(rows):
        if isinstance(row, model):
            parsed.records.append(row)
            continue
        try:
            parsed.records.append(model.model_validate(row))
        except ValidationError as exc:
            parsed.n_ignored += 1
            if len(parsed.ignored) < MAX_IGNORED_LISTED:
                path, reason = _describe(exc)
                parsed.ignored.append(IgnoredRecord(index=index, field=path, reason=reason))
    return parsed
