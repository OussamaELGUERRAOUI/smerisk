"""Shared tools for simulated formulas: block size, quantile keys, variance shares, and result rounding."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from smerisk.core.envelope import Warning
from smerisk.core.warnings import WarningCode

FloatArray = NDArray[np.float64]

DEFAULT_QUANTILES = [0.05, 0.10, 0.50, 0.90, 0.95]

CHUNK_SIZE = 10_000
"""Part of the reproducibility contract: changing this alters results for the same seed."""


def chunks(n: int) -> list[tuple[int, int]]:
    """``(start, size)`` of each block of at most ``CHUNK_SIZE`` simulations."""
    return [(start, min(CHUNK_SIZE, n - start)) for start in range(0, n, CHUNK_SIZE)]


class VarianceShares:
    """Streaming Euler allocation: share of each part in the variance of the sum of parts, Cov(X_i, S) / Var(S).

    Shares sum to 1. Sums are centered on the first block's means for numerical stability.
    """

    def __init__(self, n_parts: int) -> None:
        self._n = 0
        self._shift_parts, self._shift_total = np.zeros(n_parts), 0.0
        self._sum_parts, self._sum_cross = np.zeros(n_parts), np.zeros(n_parts)
        self._sum_total = self._sum_total_sq = 0.0

    def add(self, parts: FloatArray) -> None:
        """``parts``: one row per part, one column per simulation."""
        total = parts.sum(axis=0)
        if self._n == 0:
            self._shift_parts, self._shift_total = parts.mean(axis=1), float(total.mean())
        dp, dt = parts - self._shift_parts[:, None], total - self._shift_total
        self._sum_parts += dp.sum(axis=1)
        self._sum_cross += dp @ dt
        self._sum_total += float(dt.sum())
        self._sum_total_sq += float(dt @ dt)
        self._n += parts.shape[1]

    def shares(self) -> FloatArray:
        n = self._n
        var = (self._sum_total_sq - self._sum_total**2 / n) / (n - 1)
        if var <= 0:
            return np.zeros_like(self._sum_parts)
        cov = (self._sum_cross - self._sum_parts * self._sum_total / n) / (n - 1)
        return cov / var


def quantile_key(level: float) -> str:
    return f"{level:g}"


def quantile_cents(x: FloatArray, levels: Sequence[float]) -> dict[str, float]:
    """Quantiles of an amount sample, rounded to the cent, keyed by level."""
    return dict(zip(map(quantile_key, levels), cents(np.quantile(x, levels)), strict=True))


def high_uncertainty(x: FloatArray) -> list[Warning]:
    """``HIGH_UNCERTAINTY`` when the 90% interval is wider than the absolute median."""
    q05, median, q95 = np.quantile(x, [0.05, 0.5, 0.95])
    if q95 - q05 > abs(median):
        return [
            Warning(
                code=WarningCode.HIGH_UNCERTAINTY,
                message=f"90% interval ({q95 - q05:.2f}) is wider than the central value ({median:.2f})",
            )
        ]
    return []


def cent(x: float | np.floating[Any]) -> float:
    return round(float(x), 2) + 0.0


def cents(x: FloatArray) -> list[float]:
    values: list[float] = (np.round(x, 2) + 0.0).tolist()  # + 0.0 avoids "-0.0" in results
    return values
