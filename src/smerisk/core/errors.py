"""Errors: InvalidInput (422), InputTooLarge (413), ComputationImpossible (409) (§2.2), and limits (§2.6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MAX_SIMULATIONS = 100_000
MAX_COMPONENTS = 200
MAX_PERIODS = 60
MAX_OBSERVATIONS = 50_000

Path = tuple[str | int, ...]


@dataclass(frozen=True)
class FieldError:
    path: Path
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": list(self.path), "message": self.message}


class SmeriskError(Exception):
    http_status = 500


class InvalidInput(SmeriskError):
    """Invalid input type, range, or inconsistency between fields."""

    http_status = 422

    def __init__(self, errors: Sequence[FieldError]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in self.errors))


class InputTooLarge(SmeriskError):
    """Input exceeds the limits in §2.6."""

    http_status = 413

    def __init__(self, field: str, size: int, limit: int) -> None:
        self.field, self.size, self.limit = field, size, limit
        super().__init__(f"{field}: {size} exceeds the limit of {limit}")


class ComputationImpossible(SmeriskError):
    """Computation is impossible with this data (non-positive-definite matrix, unstable queue, etc.)."""

    http_status = 409

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code, self.details = code, details or {}
        super().__init__(f"{code}: {message}")


def check_limit(field: str, size: int, limit: int) -> None:
    if size > limit:
        raise InputTooLarge(field, size, limit)
