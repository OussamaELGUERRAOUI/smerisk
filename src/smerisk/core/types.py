"""Common types (§2.4): periods and levels."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
WEEK_PATTERN = r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$"

Period = Annotated[str, StringConstraints(pattern=f"{MONTH_PATTERN}|{WEEK_PATTERN}")]
"""Month ``YYYY-MM`` or ISO week ``YYYY-Www``."""

Month = Annotated[str, StringConstraints(pattern=MONTH_PATTERN)]
"""Month ``YYYY-MM``."""

Rate = Annotated[float, Field(ge=0, le=1)]
"""Rate between 0 and 1, inclusive."""

Level = Annotated[float, Field(gt=0, lt=1)]
"""Confidence level or quantile, strictly between 0 and 1."""


def period_kind(period: str) -> Literal["month", "week"]:
    return "week" if "W" in period else "month"


def month_index(month: str) -> int:
    """Months since year 0, so that consecutive months differ by 1."""
    year, number = month.split("-")
    return int(year) * 12 + int(number) - 1


def month_from_index(index: int) -> str:
    """Inverse of ``month_index``."""
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_range(first: str, last: str) -> list[str]:
    """Consecutive months from ``first`` to ``last``, inclusive; empty if ``last`` is before ``first``."""
    return [month_from_index(i) for i in range(month_index(first), month_index(last) + 1)]
