# `beta_binomial`

## Purpose

Estimates a binary-event probability from counts while retaining a prior and a credible interval. Examples include
whether a project is renewed, an invoice is paid on time, or a sales opportunity converts.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Eight renewals out of ten missions, with a prior worth five cases at 60%:

```python
from smerisk import beta_binomial

result = beta_binomial(successes=8, trials=10, prior={"mean": 0.6, "strength": 5})
print(round(result.result.mean, 4))  # 0.7333
print(
    {k: round(v, 4) for k, v in result.result.credible_interval.model_dump().items()}
)  # {'lower': 0.5343, 'upper': 0.896}
print(round(result.result.prior_weight, 4))  # 0.3333: the prior still weighs a third
print(result.result.posterior.model_dump())  # {'alpha': 11.0, 'beta': 4.0}
```

`posterior` can be passed directly as `renewal_probability_beta` to `utilization_forecast`.

## HTTP

`POST /v1/bayes/beta-binomial` takes the body below and returns the response envelope.

```json
{
  "successes": 8,
  "trials": 10,
  "prior": {"mean": 0.6, "strength": 5},
  "credible_level": 0.9
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope. `assumptions.prior` gives
the prior `alpha` and `beta` actually used and its `source` (`alpha_beta`, `mean_strength`, or `default`).

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `successes` | integer ≥ 0 | Yes | Number of observed successes, `r` |
| `trials` | integer ≥ successes | Yes | Number of observations, `n`; 0 returns the prior |
| `prior` | `{alpha, beta}` or `{mean, strength}` | No | Uniform Beta(1, 1) prior if absent |
| `prior.alpha`, `prior.beta` | positive numbers | For parameter form | Beta prior shape parameters |
| `prior.mean` | rate in (0, 1) | For mean/strength form | Prior probability `p0` |
| `prior.strength` | positive number | For mean/strength form | Prior effective sample size `s` |
| `credible_level` | level in (0, 1) | No | Default `0.9` |

When `prior` is omitted, the specified contract emits `DEFAULT_PRIOR`.

## Calculation

Convert mean/strength into `alpha = p0 * s` and `beta = (1 - p0) * s`. The posterior is conjugate:

```text
p | data ~ Beta(alpha + r, beta + n - r)
posterior_mean = (alpha + r) / (alpha + beta + n)
```

The credible interval is the central interval formed from the posterior Beta quantiles.

Reference: Gelman et al., *Bayesian Data Analysis*, 3rd ed., CRC Press, 2013 (Beta-Binomial conjugacy).

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `posterior.alpha`, `posterior.beta` | Posterior Beta parameters |
| `mean` | Posterior mean probability |
| `credible_interval` | Lower and upper posterior quantiles at the requested level |
| `observed_rate` | Empirical rate `successes / trials`; `null` when `trials` is 0 |
| `prior_weight` | Prior share `(alpha + beta) / (alpha + beta + trials)` |

## Warnings and errors

- `DEFAULT_PRIOR`: the uniform prior was used because none was supplied.
- `422` (`InvalidInput`): negative counts, successes greater than trials, invalid prior parameters (including a
  `mean` of exactly 0 or 1), or a credible level outside (0, 1).

## Limitations

The Beta-Binomial model represents exchangeable binary trials with a shared probability. It does not account for
changing conditions, dependencies between trials, or systematic differences across groups.
