"""ForecastLogEntry (§10.5)."""

from __future__ import annotations

import datetime as dt
from typing import Literal, Self

from pydantic import model_validator

from smerisk.contracts._base import Record, Ref
from smerisk.core.types import Level, Rate


class ForecastLogEntry(Record):
    """A forecast returned by the API and stored by the application, sent back with the actual value once known.

    ``PROBABILITY``: ``p`` that an event happens; ``outcome`` is 0 or 1. ``INTERVAL``: ``lower`` and/or ``upper`` at
    ``level`` (one bound alone for a one-sided threshold such as a cash floor); ``outcome`` is the actual value.
    """

    forecast_ref: Ref
    made_at: dt.datetime
    """A date alone is read as midnight."""
    target: Ref
    """What was forecast, for example a period or an indicator."""
    kind: Literal["PROBABILITY", "INTERVAL"]
    p: Rate | None = None
    lower: float | None = None
    upper: float | None = None
    level: Level | None = None
    outcome: float | None = None
    """``None`` while the actual value is not known."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "PROBABILITY":
            if self.p is None:
                raise ValueError("a PROBABILITY forecast requires p")
            if (self.lower, self.upper, self.level) != (None, None, None):
                raise ValueError("lower, upper, and level only apply to INTERVAL forecasts")
            if self.outcome not in (None, 0, 1):
                raise ValueError("the outcome of a PROBABILITY forecast is 0 or 1")
        else:
            if self.p is not None:
                raise ValueError("p only applies to PROBABILITY forecasts")
            if self.level is None or (self.lower is None and self.upper is None):
                raise ValueError("an INTERVAL forecast requires level and at least one of lower and upper")
            if self.lower is not None and self.upper is not None and self.lower > self.upper:
                raise ValueError("lower is above upper")
        return self

    @property
    def exceeded(self) -> bool | None:
        """INTERVAL: the actual value fell outside the interval; ``None`` while unknown or for a probability."""
        if self.kind != "INTERVAL" or self.outcome is None:
            return None
        below = self.lower is not None and self.outcome < self.lower
        above = self.upper is not None and self.outcome > self.upper
        return below or above
