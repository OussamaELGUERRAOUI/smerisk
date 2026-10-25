---
title: Bühlmann-Straub credibility in Python
description: Blend a group's observed rate with a reference rate by exposure, so that small groups are not judged on noise.
---

# Bühlmann-Straub credibility in Python

Function: `credibility`

## Purpose

Blends a group's observed rate with a reference rate according to the amount of exposure, reducing noisy estimates
for small groups. For example, an SME can estimate a small site's absence rate using comparable sites as a reference.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Absence rates of five sites over two months; `value` is absence days / theoretical days and `weight` is
theoretical days.

```python
from smerisk import credibility

result = credibility(
    mode="estimate",
    groups=[
        {"id": "site-a", "observations": [{"value": 0.06, "weight": 400}, {"value": 0.08, "weight": 380}]},
        {"id": "site-b", "observations": [{"value": 0.03, "weight": 900}, {"value": 0.04, "weight": 950}]},
        {"id": "site-c", "observations": [{"value": 0.15, "weight": 60}, {"value": 0.05, "weight": 55}]},
        {"id": "site-d", "observations": [{"value": 0.05, "weight": 300}, {"value": 0.06, "weight": 320}]},
        {"id": "site-e", "observations": [{"value": 0.04, "weight": 500}, {"value": 0.05, "weight": 480}]},
    ],
)
site_c = result.result.groups[2]
print(round(site_c.observed_mean, 4))  # 0.1022: small site, noisy rate
print(round(site_c.credibility, 2))  # 0.22: little exposure, little weight on its own data
print(round(site_c.estimate, 4))  # 0.0646: pulled toward the reference mean
print(round(result.result.parameters.mu, 4))  # 0.0539
print(round(result.result.parameters.k, 1))  # 404.4: reusable as credibility_constant
```

With a single group, use `mode="reference"` and pass `reference_mean` and `credibility_constant` (published ratios,
or `parameters.mu` and `parameters.k` from an earlier `estimate` run).

## HTTP

`POST /v1/bayes/credibility` takes the body below and returns the response envelope.

```json
{
  "mode": "reference",
  "groups": [
    {"id": "restaurant", "observations": [{"value": 0.08, "weight": 160}, {"value": 0.12, "weight": 160}]}
  ],
  "reference_mean": 0.05,
  "credibility_constant": 400
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `mode` | `estimate` or `reference` | Yes | Select parameter estimation or supplied reference parameters |
| `groups[].id` | string | Yes | Opaque group identifier |
| `groups[].observations[]` | non-empty list of `{value, weight}` | Yes | `value` is a rate; `weight` > 0 is exposure in matching units |
| `reference_mean` | number | In `reference` mode | Reference rate μ |
| `credibility_constant` | number > 0 | In `reference` mode | k = within-group variance / between-group variance |
| `min_groups` | integer ≥ 2 | No | Recommended minimum groups in `estimate` mode; default 5; fewer emits `SMALL_SAMPLE` |

## Calculation

For group `i`, total exposure and weighted mean are:

```text
w[i] = sum(w[i,j])
Xbar[i] = sum(w[i,j] * X[i,j]) / w[i]
Z[i] = w[i] / (w[i] + k)
estimate[i] = Z[i] * Xbar[i] + (1 - Z[i]) * mu
```

In `estimate` mode, with `r` groups, `n[i]` observations per group, total exposure `w = sum(w[i])`, and overall
weighted mean `Xbar = sum(w[i] * Xbar[i]) / w`, the Bühlmann-Straub estimators are:

```text
v_hat = sum_i sum_j(w[i,j] * (X[i,j] - Xbar[i])^2) / sum_i(n[i] - 1)
a_hat = (sum_i(w[i] * (Xbar[i] - Xbar)^2) - (r - 1) * v_hat)
        / (w - sum_i(w[i]^2) / w)
k = v_hat / a_hat
mu = sum_i(Z[i] * Xbar[i]) / sum_i(Z[i])
```

If estimated between-group variance `a_hat` is non-positive, credibility is zero for every group, μ is the
exposure-weighted mean, `a` is reported as 0, and `k` as `null` (infinite).

Reference: Bühlmann and Straub, “Glaubwürdigkeit für Schadensätze,” *Mitteilungen der Vereinigung Schweizerischer
Versicherungsmathematiker*, 1970.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `groups[].exposure` | Total group exposure `w[i]` |
| `groups[].observed_mean` | Exposure-weighted group mean |
| `groups[].credibility` | `Z` in [0, 1], the weight assigned to the group's own data |
| `groups[].estimate` | Credibility-weighted rate estimate |
| `parameters.mu` | Reference mean used |
| `parameters.v` | Estimated within-group variance; `null` in `reference` mode |
| `parameters.a` | Estimated between-group variance; `null` in `reference` mode |
| `parameters.k` | Credibility constant; can be retained for reference-mode reuse; `null` when `a` is 0 |

## Warnings and errors

- `NO_BETWEEN_GROUP_VARIANCE`: groups do not differ more than expected by within-group variation.
- `SMALL_SAMPLE`: in `estimate` mode, fewer groups than `min_groups`.
- `422` (`InvalidInput`): non-positive weights, empty observation lists, duplicate group ids, fewer than 2 groups in
  `estimate` mode, or `reference_mean` / `credibility_constant` missing in `reference` mode.
- `409` (`ComputationImpossible`, `WITHIN_VARIANCE_NOT_ESTIMABLE`): every group has a single observation.
- `413` (`InputTooLarge`): more than 50,000 observations.

## Limitations

The model assumes comparable groups and meaningful exposure weights. It is not a causal model and should not be
used to rank groups without considering exposure quality, uncertainty, and relevant differences between groups.
