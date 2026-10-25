"""Distributions for uncertain quantities (§2.4).

Each distribution maps standard normal draws ``z`` to values through its inverse CDF:
this makes it possible to correlate components (Gaussian copula, §3.1). Transformations are vectorized:
``from_normal`` processes a batch of distributions of one type per call, with one row of ``z`` per distribution.

Expensive inverse CDFs (Beta, PERT, truncated normal: 50 to 100 times slower than an exponential) are
tabulated once per parameter set on a regular z grid and then linearly interpolated. The error
stays below 2e-6 of the distribution range for |z| ≤ 6 (all but one draw in 500 million), well below
the Monte Carlo error.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Any, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, PrivateAttr, model_validator
from scipy import special, stats

from smerisk.core.envelope import InputModel
from smerisk.core.types import Rate

FloatArray = NDArray[np.float64]

_EPS = 1e-15
Z_90 = float(special.ndtri(0.9))


def _uniform(z: FloatArray) -> FloatArray:
    """Φ(z), clipped to ]0, 1[ so inverse CDFs remain finite."""
    return np.clip(np.asarray(special.ndtr(z), dtype=np.float64), _EPS, 1.0 - _EPS)


def _column(dists: Sequence[object], attr: str) -> FloatArray:
    return np.array([getattr(d, attr) for d in dists], dtype=np.float64)[:, None]


# Tabulated inverse grid. It is part of the reproducibility contract (changing it requires a major version).
_Z_MAX = float(-special.ndtri(_EPS))  # Same bounds as _uniform
_GRID_POINTS = 4097
_GRID_Z = np.linspace(-_Z_MAX, _Z_MAX, _GRID_POINTS)
_GRID_STEP = 2 * _Z_MAX / (_GRID_POINTS - 1)


def _frozen(x: Any) -> FloatArray:
    table = np.asarray(x, dtype=np.float64)
    table.setflags(write=False)
    return table


@lru_cache(maxsize=512)
def _beta_table(alpha: float, beta: float) -> FloatArray:
    return _frozen(special.betaincinv(alpha, beta, special.ndtr(_GRID_Z)))


@lru_cache(maxsize=512)
def _truncnorm_table(a: float, b: float) -> FloatArray:
    """Standard normal truncated to [a, b]."""
    return _frozen(stats.truncnorm.ppf(np.clip(special.ndtr(_GRID_Z), _EPS, 1 - _EPS), a, b))


def _interpolate(tables: Sequence[FloatArray], z: FloatArray) -> FloatArray:
    """Read row i of ``z`` from ``tables[i]`` using linear interpolation."""
    pos = (np.clip(z, -_Z_MAX, _Z_MAX) + _Z_MAX) / _GRID_STEP
    i = np.minimum(pos.astype(np.intp), _GRID_POINTS - 2)
    w = pos - i
    grid = np.stack(tables)
    rows = np.arange(len(tables))[:, None]
    lo: FloatArray = grid[rows, i]
    hi: FloatArray = grid[rows, i + 1]
    return lo + w * (hi - lo)


class _Law(InputModel):
    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        raise NotImplementedError


class _MinModeMax(_Law):
    min: float
    mode: float
    max: float

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if not self.min <= self.mode <= self.max or self.min == self.max:
            raise ValueError("require min ≤ mode ≤ max and min < max")
        return self


class Fixed(_Law):
    type: Literal["fixed"] = "fixed"
    value: float

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        return np.broadcast_to(_column(dists, "value"), z.shape)


class Normal(_Law):
    """Normal distribution, truncated to [min, max] when either bound is provided."""

    type: Literal["normal"] = "normal"
    mean: float
    sd: float = Field(ge=0)
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError("require min < max")
        return self

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        mean, sd = _column(dists, "mean"), _column(dists, "sd")
        lo = np.array([-np.inf if d.min is None else d.min for d in dists])[:, None]
        hi = np.array([np.inf if d.max is None else d.max for d in dists])[:, None]
        x = mean + sd * z
        rows = ((np.isfinite(lo) | np.isfinite(hi)) & (sd > 0)).ravel()
        if rows.any():
            m, s = mean[rows], sd[rows]
            a, b = ((lo[rows] - m) / s).ravel(), ((hi[rows] - m) / s).ravel()
            tables = [_truncnorm_table(float(ai), float(bi)) for ai, bi in zip(a, b, strict=True)]
            x[rows] = m + s * _interpolate(tables, z[rows])
        # With zero standard deviation, clamp the mean to the bounds.
        return np.clip(x, lo, hi)


class Lognormal(_Law):
    """Log-normal parameterized by median and 90th percentile: mu = ln(median), sigma = ln(p90/median)/z_0.9."""

    type: Literal["lognormal"] = "lognormal"
    median: float = Field(gt=0)
    p90: float

    @model_validator(mode="after")
    def _check_p90(self) -> Self:
        if self.p90 <= self.median:
            raise ValueError("p90 must be greater than the median")
        return self

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        median = _column(dists, "median")
        sigma = np.log(_column(dists, "p90") / median) / Z_90
        return np.asarray(median * np.exp(sigma * z), dtype=np.float64)


class Triangular(_MinModeMax):
    type: Literal["triangular"] = "triangular"

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        a, c, b = _column(dists, "min"), _column(dists, "mode"), _column(dists, "max")
        u = _uniform(z)
        left = a + np.sqrt(u * (b - a) * (c - a))
        right = b - np.sqrt((1 - u) * (b - a) * (b - c))
        return np.where(u < (c - a) / (b - a), left, right)


class Pert(_MinModeMax):
    """Beta-PERT: alpha = 1 + 4(m − a)/(b − a), beta = 1 + 4(b − m)/(b − a), scaled to [a, b]."""

    type: Literal["pert"] = "pert"

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        a, b = _column(dists, "min"), _column(dists, "max")
        tables = [_beta_table(*d.shape()) for d in dists]
        return a + (b - a) * _interpolate(tables, z)

    def shape(self) -> tuple[float, float]:
        """Parameters (alpha, beta) of the underlying Beta distribution."""
        width = self.max - self.min
        return 1 + 4 * (self.mode - self.min) / width, 1 + 4 * (self.max - self.mode) / width


class Uniform(_Law):
    type: Literal["uniform"] = "uniform"
    min: float
    max: float

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.min >= self.max:
            raise ValueError("require min < max")
        return self

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        a, b = _column(dists, "min"), _column(dists, "max")
        return a + (b - a) * _uniform(z)


class Bernoulli(_Law):
    """Equals ``value`` with probability ``p`` and 0 otherwise."""

    type: Literal["bernoulli"] = "bernoulli"
    p: Rate
    value: float

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        # P(z > Φ⁻¹(1 − p)) = p; infinite thresholds handle p = 0 and p = 1 exactly.
        threshold = special.ndtri(1 - _column(dists, "p"))
        return np.where(z > threshold, _column(dists, "value"), 0.0)


class Beta(_Law):
    type: Literal["beta"] = "beta"
    alpha: float = Field(gt=0)
    beta: float = Field(gt=0)

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        return _interpolate([_beta_table(d.alpha, d.beta) for d in dists], z)


class Empirical(_Law):
    """Resample historical data with replacement from ``samples``."""

    type: Literal["empirical"] = "empirical"
    samples: list[float] = Field(min_length=20)
    _sorted: FloatArray = PrivateAttr()

    def model_post_init(self, context: Any, /) -> None:
        self._sorted = np.sort(np.asarray(self.samples, dtype=np.float64))

    @classmethod
    def _from_normal(cls, dists: Sequence[Self], z: FloatArray) -> FloatArray:
        u = _uniform(z)
        out = np.empty_like(z)
        for i, d in enumerate(dists):
            n = len(d._sorted)
            out[i] = d._sorted[np.minimum((u[i] * n).astype(np.intp), n - 1)]
        return out


Distribution = Annotated[
    Fixed | Normal | Lognormal | Triangular | Pert | Uniform | Bernoulli | Beta | Empirical,
    Field(discriminator="type"),
]


def from_normal(dists: Sequence[Distribution], z: FloatArray) -> FloatArray:
    """Transform row i of ``z`` (standard normals) using ``dists[i]``; output has the same shape as ``z``."""
    rows: dict[type[_Law], list[int]] = {}
    for i, d in enumerate(dists):
        rows.setdefault(type(d), []).append(i)
    if len(rows) == 1:
        ((law, _),) = rows.items()
        return law._from_normal(dists, z)
    out = np.empty_like(z)
    for law, idx in rows.items():
        out[idx] = law._from_normal([dists[i] for i in idx], z[idx])
    return out


def sample(dist: Distribution, rng: np.random.Generator, n: int) -> FloatArray:
    """Draw ``n`` independent samples from ``dist``."""
    return np.array(from_normal([dist], rng.standard_normal((1, n)))[0])
