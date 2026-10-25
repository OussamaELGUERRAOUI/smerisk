"""Estimate: a derived parameter with its source, method, and data (§11, §11.5)."""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

V = TypeVar("V")

Source = Literal["estimated", "default", "override"]


class Estimate(BaseModel, Generic[V]):
    """A parameter passed to a formula, and where it comes from."""

    value: V
    source: Source
    method: str
    n_points: int = 0
    """Data points used; 0 for a default or an override."""
    first: str | None = None
    """First period (or date) of the data used."""
    last: str | None = None
