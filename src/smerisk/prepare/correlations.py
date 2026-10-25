"""Shrink Spearman correlations toward 0 (§11.2)."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence

import numpy as np
from scipy import stats

from smerisk.core.correlation import Correlation
from smerisk.core.envelope import Warning
from smerisk.core.warnings import WarningCode
from smerisk.prepare._base import Estimate

MIN_COMMON_MONTHS = 12
SHRINKAGE_MONTHS = 10
"""rho_used = rho_observed × n / (n + 10)."""


def pair_key(a: str, b: str) -> str:
    """Name of a correlation in ``derived_parameters`` and ``overrides``: ``"overtime~sick_pay"``."""
    return f"{a}~{b}"


def default_for(defaults: Mapping[str, float], a: str, b: str) -> float:
    """Default correlation of a pair, whichever order its key uses; 0 when none is given."""
    return defaults.get(pair_key(a, b), defaults.get(pair_key(b, a), 0.0))


def shrunk_spearman(
    a: Mapping[str, float], b: Mapping[str, float], *, default: float = 0.0, name: str = "series"
) -> tuple[Estimate[float], Warning | None]:
    """Spearman correlation of two monthly series over their common months, shrunk toward 0."""
    common = sorted(set(a) & set(b))
    n = len(common)
    if n >= MIN_COMMON_MONTHS:
        x, y = np.array([a[m] for m in common]), np.array([b[m] for m in common])
        if np.ptp(x) > 0 and np.ptp(y) > 0:
            rho = float(stats.spearmanr(x, y).statistic)
            return Estimate(
                value=rho * n / (n + SHRINKAGE_MONTHS),
                source="estimated",
                method=f"Spearman {rho:.3f} on common months, shrunk by n / (n + {SHRINKAGE_MONTHS})",
                n_points=n,
                first=common[0],
                last=common[-1],
            ), None
        reason = "a constant series"
    else:
        reason = f"{n} common months, at least {MIN_COMMON_MONTHS} needed"
    warning = Warning(code=WarningCode.DEFAULT_CORRELATION, message=f"{name}: {reason}, default {default} used")
    return Estimate(value=default, source="default", method=f"default of the use case ({reason})"), warning


def estimate_correlations(
    series: Mapping[str, Mapping[str, float]], *, defaults: Mapping[str, float] | None = None
) -> tuple[dict[str, Estimate[float]], list[Warning]]:
    """Every pair of monthly series, keyed by ``pair_key``; ``defaults`` maps a pair key to its fallback value."""
    defaults = defaults or {}
    pairs: dict[str, Estimate[float]] = {}
    warnings: list[Warning] = []
    for a, b in itertools.combinations(series, 2):
        key = pair_key(a, b)
        pairs[key], warning = shrunk_spearman(series[a], series[b], default=default_for(defaults, a, b), name=key)
        if warning is not None:
            warnings.append(warning)
    return pairs, warnings


def correlation_matrix(names: Sequence[str], values: Mapping[str, float]) -> Correlation:
    """Matrix from pair values (after any override); a pair absent from ``values`` is 0.

    The formula makes the matrix positive definite if needed (§2.4).
    """
    matrix = np.eye(len(names))
    for (i, a), (j, b) in itertools.combinations(enumerate(names), 2):
        matrix[i, j] = matrix[j, i] = values.get(pair_key(a, b), values.get(pair_key(b, a), 0.0))
    return Correlation(variables=list(names), matrix=matrix.tolist())
