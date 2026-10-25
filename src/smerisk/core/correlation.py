"""Rank-correlation matrices: validation, positive-definite adjustment, and Cholesky (§2.4, §3.1).

Provided correlations are rank correlations (Spearman). In a Gaussian copula, rank correlation rho corresponds
to correlation r = 2·sin(π·rho/6) between the underlying normal variables; this converted matrix is factorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, model_validator

from smerisk.core.envelope import InputModel
from smerisk.core.errors import ComputationImpossible

FloatArray = NDArray[np.float64]

MAX_ADJUSTMENT = 0.05
"""Maximum accepted adjustment (Spearman scale) to make a matrix positive definite; larger changes raise 409."""

_EIGEN_FLOOR = 1e-8


class Correlation(InputModel):
    variables: list[str] = Field(min_length=2)
    matrix: list[list[float]]

    @model_validator(mode="after")
    def _check_matrix(self) -> Self:
        n = len(self.variables)
        if len(set(self.variables)) != n:
            raise ValueError("variables must be unique")
        if len(self.matrix) != n or any(len(row) != n for row in self.matrix):
            raise ValueError(f"matrix must be {n} × {n}")
        m = self.as_array()
        if not np.allclose(m, m.T, rtol=0, atol=1e-9):
            raise ValueError("matrix must be symmetric")
        if not np.allclose(np.diag(m), 1, rtol=0, atol=1e-9):
            raise ValueError("diagonal entries must equal 1")
        if np.abs(m).max() > 1:
            raise ValueError("correlations must be between -1 and 1")
        return self

    def as_array(self) -> FloatArray:
        return np.asarray(self.matrix, dtype=np.float64)


@dataclass(frozen=True)
class PreparedCorrelation:
    cholesky: FloatArray
    """L such that L·Lᵀ is the correlation matrix of the underlying normal variables."""
    spearman: FloatArray
    """Rank correlations actually used (after any adjustment)."""
    adjustment: float
    """Maximum adjustment introduced; 0 if the matrix was not changed."""


def spearman_to_pearson(rho: FloatArray) -> FloatArray:
    r = 2 * np.sin(np.pi * rho / 6)
    np.fill_diagonal(r, 1.0)
    return r


def pearson_to_spearman(r: FloatArray) -> FloatArray:
    rho = 6 / np.pi * np.arcsin(r / 2)
    np.fill_diagonal(rho, 1.0)
    return rho


def nearest_correlation(m: FloatArray) -> FloatArray:
    """Eigenvalues clipped to a positive floor, then the diagonal reset to 1."""
    w, v = np.linalg.eigh(m)
    a = (v * np.maximum(w, _EIGEN_FLOOR)) @ v.T
    d = 1 / np.sqrt(np.diag(a))
    a = a * d[:, None] * d[None, :]
    np.fill_diagonal(a, 1.0)
    return (a + a.T) / 2


def prepare_correlation(spearman: FloatArray, max_adjustment: float = MAX_ADJUSTMENT) -> PreparedCorrelation:
    pearson = spearman_to_pearson(spearman)
    try:
        return PreparedCorrelation(np.linalg.cholesky(pearson), spearman, 0.0)
    except np.linalg.LinAlgError:
        pass
    adjusted = nearest_correlation(pearson)
    used = pearson_to_spearman(adjusted)
    adjustment = float(np.abs(used - spearman).max())
    if adjustment > max_adjustment:
        min_eigenvalue = float(np.linalg.eigvalsh(spearman)[0])
        raise ComputationImpossible(
            "CORRELATION_NOT_POSITIVE_DEFINITE",
            f"correlation matrix is not positive definite (smallest eigenvalue {min_eigenvalue:.4f})",
            {"min_eigenvalue": min_eigenvalue},
        )
    return PreparedCorrelation(np.linalg.cholesky(adjusted), used, adjustment)
