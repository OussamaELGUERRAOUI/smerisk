"""benford: Benford's law test of leading digits with Nigrini's MAD (§7.1).

A strong deviation flags amounts that may be invented, rounded, or split to stay under a threshold. It is a signal
to examine, never proof of fraud.

    P(d) = log10(1 + 1/d)        MAD = (1/K) * sum(|observed_d - P(d)|)        K = 9 or 90
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal, Self

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy import stats

from smerisk.core.envelope import InputModel, Result, Warning, envelope, parse_input
from smerisk.core.errors import MAX_OBSERVATIONS, ComputationImpossible, check_limit
from smerisk.core.warnings import WarningCode

Conformity = Literal["CLOSE", "ACCEPTABLE", "MARGINAL", "NONCONFORMITY"]

# Nigrini's MAD bands: upper bounds of CLOSE, ACCEPTABLE, and MARGINAL.
MAD_BANDS = {"first_digit": (0.006, 0.012, 0.015), "first_two_digits": (0.0012, 0.0018, 0.0022)}
MIN_FOR_CONCLUSION = 100
MIN_RECOMMENDED = 300
Z_SUSPICIOUS = 1.96


class BenfordInput(InputModel):
    amounts: list[float] = Field(min_length=1)
    min_amount: float = Field(10, ge=0)
    test: Literal["first_digit", "first_two_digits"] = "first_digit"
    labels: list[str] | None = None
    """Opaque identifiers, same length as ``amounts``, returned for amounts starting with a suspicious digit."""

    @model_validator(mode="after")
    def _check_labels(self) -> Self:
        if self.labels is not None and len(self.labels) != len(self.amounts):
            raise ValueError("labels must have the same length as amounts")
        return self


class Digit(BaseModel):
    digit: int
    observed: float
    expected: float
    z: float
    """Nigrini's z statistic with continuity correction (absolute value)."""


class BenfordResult(BaseModel):
    n_used: int
    n_excluded: int
    digits: list[Digit]
    mad: float
    conformity: Conformity | None
    """``None`` below 100 amounts: too few for a conclusion."""
    chi2: float
    p_value: float
    suspicious_digits: list[int]
    """Digits observed significantly more often than expected (z > 1.96)."""
    flagged_labels: list[str] | None


def benford(params: BenfordInput | Mapping[str, Any] | None = None, /, **fields: Any) -> Result[BenfordResult]:
    started = time.perf_counter()
    inp = parse_input(BenfordInput, params, fields)
    check_limit("amounts", len(inp.amounts), MAX_OBSERVATIONS)

    two = inp.test == "first_two_digits"
    absolute = np.abs(np.asarray(inp.amounts))
    # Leading digits read from whole cents, so that float rounding cannot shift them (299.99999… stays 29).
    cents = np.rint(absolute * 100).astype(np.int64)
    used = (absolute >= inp.min_amount) & (cents >= (10 if two else 1))
    if not used.any():
        raise ComputationImpossible(
            "NO_ELIGIBLE_AMOUNTS", f"no amount is at least {inp.min_amount:g} once absolute values are taken"
        )
    leading = np.array([int(str(c)[: 2 if two else 1]) for c in cents[used]])

    digits = np.arange(10, 100) if two else np.arange(1, 10)
    expected = np.log10(1 + 1 / digits)
    n = int(used.sum())
    counts = np.bincount(leading, minlength=digits[-1] + 1)[digits[0] :]
    observed = counts / n
    diff = np.abs(observed - expected)
    correction = np.where(1 / (2 * n) < diff, 1 / (2 * n), 0.0)
    z = (diff - correction) / np.sqrt(expected * (1 - expected) / n)
    mad = float(diff.mean())
    chi2 = float(((counts - n * expected) ** 2 / (n * expected)).sum())
    suspicious = [
        int(d) for d, zz, o, e in zip(digits, z, observed, expected, strict=True) if zz > Z_SUSPICIOUS and o > e
    ]

    flagged = None
    if inp.labels is not None:
        labels = np.asarray(inp.labels)[used]
        flagged = [str(label) for label, d in zip(labels, leading, strict=True) if d in set(suspicious)]

    warnings: list[Warning] = []
    if n < MIN_RECOMMENDED:
        warnings.append(
            Warning(
                code=WarningCode.SMALL_SAMPLE,
                message=f"{n} amounts, recommended minimum {MIN_RECOMMENDED}"
                + (f"; no conclusion below {MIN_FOR_CONCLUSION}" if n < MIN_FOR_CONCLUSION else ""),
            )
        )

    return envelope(
        BenfordResult(
            n_used=n,
            n_excluded=len(inp.amounts) - n,
            digits=[
                Digit(digit=int(d), observed=float(o), expected=float(e), z=float(zz))
                for d, o, e, zz in zip(digits, observed, expected, z, strict=True)
            ],
            mad=mad,
            conformity=_conformity(mad, MAD_BANDS[inp.test]) if n >= MIN_FOR_CONCLUSION else None,
            chi2=chi2,
            p_value=float(stats.chi2.sf(chi2, len(digits) - 1)),
            suspicious_digits=suspicious,
            flagged_labels=flagged,
        ),
        assumptions={
            "test": inp.test,
            "min_amount": inp.min_amount,
            "mad_bands": dict(zip(("CLOSE", "ACCEPTABLE", "MARGINAL"), MAD_BANDS[inp.test], strict=True)),
            "z_threshold": Z_SUSPICIOUS,
            "z_continuity_correction": True,
            "digits_read_from": "absolute amounts rounded to the cent",
        },
        warnings=warnings,
        method="benford-nigrini",
        started=started,
    )


def _conformity(mad: float, bands: tuple[float, float, float]) -> Conformity:
    close, acceptable, marginal = bands
    if mad < close:
        return "CLOSE"
    if mad < acceptable:
        return "ACCEPTABLE"
    if mad < marginal:
        return "MARGINAL"
    return "NONCONFORMITY"
