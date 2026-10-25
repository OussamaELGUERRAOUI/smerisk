"""credibility (Bühlmann-Straub) and beta_binomial (§5.1, §5.2): estimating rates from little data."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal, Self

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy import special

from smerisk.core.envelope import InputModel, Result, Warning, envelope, parse_input
from smerisk.core.errors import MAX_OBSERVATIONS, ComputationImpossible, FieldError, InvalidInput, check_limit
from smerisk.core.types import Level
from smerisk.core.warnings import WarningCode

# --- §5.1 Bühlmann-Straub credibility -------------------------------------------------------------------------


class Observation(InputModel):
    value: float
    """Observed rate over one period, e.g. absence days / theoretical days."""
    weight: float = Field(gt=0)
    """Exposure of the period, e.g. theoretical days."""


class Group(InputModel):
    id: str = Field(min_length=1)
    observations: list[Observation] = Field(min_length=1)


class CredibilityInput(InputModel):
    mode: Literal["estimate", "reference"]
    groups: list[Group] = Field(min_length=1)
    reference_mean: float | None = None
    credibility_constant: float | None = Field(None, gt=0)
    min_groups: int = Field(5, ge=2)

    @model_validator(mode="after")
    def _check_mode(self) -> Self:
        if self.mode == "reference" and (self.reference_mean is None or self.credibility_constant is None):
            raise ValueError("reference mode requires reference_mean and credibility_constant")
        if self.mode == "estimate" and len(self.groups) < 2:
            raise ValueError("estimate mode requires at least 2 groups")
        return self


class GroupEstimate(BaseModel):
    id: str
    exposure: float
    observed_mean: float
    credibility: float
    """Z in [0, 1]: weight given to the group's own data."""
    estimate: float


class CredibilityParameters(BaseModel):
    mu: float
    v: float | None
    """Within-group variance; ``None`` in reference mode."""
    a: float | None
    """Between-group variance; ``None`` in reference mode, 0 when groups do not differ more than chance."""
    k: float | None
    """v / a; reusable as ``credibility_constant`` in reference mode. ``None`` (infinite) when a = 0."""


class CredibilityResult(BaseModel):
    groups: list[GroupEstimate]
    parameters: CredibilityParameters


def credibility(
    params: CredibilityInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[CredibilityResult]:
    started = time.perf_counter()
    inp = parse_input(CredibilityInput, params, fields)
    check_limit("observations", sum(len(g.observations) for g in inp.groups), MAX_OBSERVATIONS)
    _check_unique_ids(inp.groups)

    values = [np.array([o.value for o in g.observations]) for g in inp.groups]
    weights = [np.array([o.weight for o in g.observations]) for g in inp.groups]
    exposure = np.array([w.sum() for w in weights])
    means = np.array([(w * x).sum() / w.sum() for x, w in zip(values, weights, strict=True)])
    warnings: list[Warning] = []

    if inp.mode == "reference":
        assert inp.reference_mean is not None and inp.credibility_constant is not None  # checked by the model
        mu, k = inp.reference_mean, inp.credibility_constant
        z = exposure / (exposure + k)
        parameters = CredibilityParameters(mu=mu, v=None, a=None, k=k)
    else:
        v, a = _buhlmann_straub(values, weights, exposure, means)
        if a <= 0:
            z = np.zeros(len(inp.groups))
            mu = float((exposure * means).sum() / exposure.sum())
            parameters = CredibilityParameters(mu=mu, v=v, a=0.0, k=None)
            warnings.append(
                Warning(
                    code=WarningCode.NO_BETWEEN_GROUP_VARIANCE,
                    message="groups do not differ more than chance: every group gets the overall mean",
                )
            )
        else:
            k = v / a
            z = exposure / (exposure + k)
            mu = float((z * means).sum() / z.sum())
            parameters = CredibilityParameters(mu=mu, v=v, a=a, k=k)
        if len(inp.groups) < inp.min_groups:
            warnings.append(
                Warning(
                    code=WarningCode.SMALL_SAMPLE,
                    message=f"{len(inp.groups)} groups, recommended minimum {inp.min_groups} to estimate v and a",
                )
            )

    estimates = z * means + (1 - z) * mu
    return envelope(
        CredibilityResult(
            groups=[
                GroupEstimate(
                    id=g.id, exposure=float(w), observed_mean=float(x), credibility=float(c), estimate=float(e)
                )
                for g, w, x, c, e in zip(inp.groups, exposure, means, z, estimates, strict=True)
            ],
            parameters=parameters,
        ),
        assumptions={
            "mode": inp.mode,
            "min_groups": inp.min_groups,
            "reference_mean": inp.reference_mean,
            "credibility_constant": inp.credibility_constant,
            "estimators": "Bühlmann-Straub unbiased" if inp.mode == "estimate" else None,
        },
        warnings=warnings,
        method="buhlmann-straub",
        started=started,
    )


def _buhlmann_straub(
    values: list[np.ndarray[Any, Any]],
    weights: list[np.ndarray[Any, Any]],
    exposure: np.ndarray[Any, Any],
    means: np.ndarray[Any, Any],
) -> tuple[float, float]:
    """Unbiased estimators of the within-group (v) and between-group (a) variances."""
    dof = sum(len(x) - 1 for x in values)
    if dof == 0:
        raise ComputationImpossible(
            "WITHIN_VARIANCE_NOT_ESTIMABLE",
            "every group has a single observation: the within-group variance cannot be estimated",
        )
    v = sum(float((w * (x - m) ** 2).sum()) for x, w, m in zip(values, weights, means, strict=True)) / dof
    total = float(exposure.sum())
    overall = float((exposure * means).sum() / total)
    a = (float((exposure * (means - overall) ** 2).sum()) - (len(values) - 1) * v) / (
        total - float((exposure**2).sum()) / total
    )
    return v, a


def _check_unique_ids(groups: list[Group]) -> None:
    seen: set[str] = set()
    errors = []
    for i, g in enumerate(groups):
        if g.id in seen:
            errors.append(FieldError(("groups", i, "id"), f"duplicate id: {g.id}"))
        seen.add(g.id)
    if errors:
        raise InvalidInput(errors)


# --- §5.2 Beta-Binomial rate ----------------------------------------------------------------------------------


class BetaPrior(InputModel):
    alpha: float = Field(gt=0)
    beta: float = Field(gt=0)


class MeanStrengthPrior(InputModel):
    """ "An opinion worth ``strength`` observed cases, with a rate of ``mean``"."""

    mean: float = Field(gt=0, lt=1)
    strength: float = Field(gt=0)


class BetaBinomialInput(InputModel):
    successes: int = Field(ge=0)
    trials: int = Field(ge=0)
    prior: BetaPrior | MeanStrengthPrior | None = None
    credible_level: Level = 0.9

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        if self.successes > self.trials:
            raise ValueError("successes cannot exceed trials")
        return self


class Posterior(BaseModel):
    alpha: float
    beta: float
    """Usable directly as ``renewal_probability_beta`` in utilization_forecast (§4.3)."""


class CredibleInterval(BaseModel):
    lower: float
    upper: float


class BetaBinomialResult(BaseModel):
    posterior: Posterior
    mean: float
    credible_interval: CredibleInterval
    observed_rate: float | None
    """successes / trials; ``None`` when there are no trials."""
    prior_weight: float
    """(alpha + beta) / (alpha + beta + trials): share of the prior in the estimate."""


def beta_binomial(
    params: BetaBinomialInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> Result[BetaBinomialResult]:
    started = time.perf_counter()
    inp = parse_input(BetaBinomialInput, params, fields)
    warnings: list[Warning] = []
    if isinstance(inp.prior, BetaPrior):
        alpha, beta, source = inp.prior.alpha, inp.prior.beta, "alpha_beta"
    elif isinstance(inp.prior, MeanStrengthPrior):
        alpha, beta, source = (
            inp.prior.mean * inp.prior.strength,
            (1 - inp.prior.mean) * inp.prior.strength,
            "mean_strength",
        )
    else:
        alpha, beta, source = 1.0, 1.0, "default"
        warnings.append(
            Warning(code=WarningCode.DEFAULT_PRIOR, message="no prior given: uniform Beta(1, 1) prior used")
        )

    r, n = inp.successes, inp.trials
    post_alpha, post_beta = alpha + r, beta + n - r
    tail = (1 - inp.credible_level) / 2
    lower, upper = (float(special.betaincinv(post_alpha, post_beta, q)) for q in (tail, 1 - tail))
    return envelope(
        BetaBinomialResult(
            posterior=Posterior(alpha=post_alpha, beta=post_beta),
            mean=post_alpha / (post_alpha + post_beta),
            credible_interval=CredibleInterval(lower=lower, upper=upper),
            observed_rate=r / n if n else None,
            prior_weight=(alpha + beta) / (alpha + beta + n),
        ),
        assumptions={
            "prior": {"alpha": alpha, "beta": beta, "source": source},
            "credible_level": inp.credible_level,
            "interval": "equal-tailed",
        },
        warnings=warnings,
        method="beta-binomial",
        started=started,
    )
