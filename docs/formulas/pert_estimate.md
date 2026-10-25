---
title: PERT three-point estimate in Python
description: Compute expected effort, total effort at confidence levels and contingency from optimistic, most-likely and pessimistic task estimates.
---

# PERT three-point estimate in Python

Function: `pert_estimate`

## Purpose

Estimates expected task effort and total effort from optimistic, most-likely, and pessimistic estimates. A small
business can use it to create a transparent effort estimate and contingency for a fixed-scope project.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

```python
from smerisk import pert_estimate

result = pert_estimate(
    tasks=[
        {"id": "analysis", "optimistic": 2, "most_likely": 3, "pessimistic": 6},
        {"id": "development", "optimistic": 5, "most_likely": 8, "pessimistic": 15},
    ],
)
print(round(result.result.total.expected, 2))  # 12.0
print(
    {k: round(v, 2) for k, v in result.result.total.at_confidence.items()}
)  # {'0.5': 12.0, '0.8': 13.51, '0.9': 14.3}
print(round(result.result.contingency, 2))  # 1.51
print([w.code.value for w in result.warnings])  # ['SMALL_SAMPLE']: fewer than 5 tasks
```

The call also accepts a dictionary (`pert_estimate({"tasks": [...]})`) or a `PertInput` object.

## HTTP

`POST /v1/pert/estimate` takes the body below and returns the response envelope.

```json
{
  "tasks": [
    {"id": "analysis", "optimistic": 2, "most_likely": 3, "pessimistic": 6}
  ],
  "confidence_levels": [0.5, 0.8, 0.9]
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope. `assumptions` lists the
sorted confidence levels, the standard normal quantiles used (`z_values`), and the independence and normal-total
assumptions.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `tasks` | list of task objects | Yes | Each task has a unique string `id` |
| `tasks[].optimistic` | number ≥ 0 | Yes | Optimistic effort, days |
| `tasks[].most_likely` | number | Yes | Most likely effort, days; `optimistic ≤ most_likely ≤ pessimistic` |
| `tasks[].pessimistic` | number | Yes | Pessimistic effort, days; strictly greater than `optimistic` |
| `confidence_levels` | list of rates | No | Default `[0.5, 0.8, 0.9]` |

## Calculation

Classical PERT estimates for task `i` are:

```text
E[i]  = (a[i] + 4*m[i] + b[i]) / 6
sd[i] = (b[i] - a[i]) / 6
E     = sum(E[i])
sd    = sqrt(sum(sd[i]^2))
effort(p) = E + z[p] * sd
```

Here `a`, `m`, and `b` are optimistic, most-likely, and pessimistic effort. `z[p]` is the standard normal quantile.
The sum assumes independent task durations and uses a normal approximation.

Reference: Malcolm, Roseboom, Clark, and Fazar, “Application of a Technique for Research and Development Program
Evaluation,” *Operations Research*, 1959.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `tasks[].expected` | Expected effort for each task, in days |
| `tasks[].sd` | Estimated task standard deviation, in days |
| `total.expected` | Sum of expected task efforts |
| `total.sd` | Standard deviation of the total under independence |
| `total.at_confidence` | Estimated effort quantiles at requested confidence levels |
| `contingency` | 80th-percentile effort minus expected effort, whatever `confidence_levels` contains |

## Warnings and errors

- `SMALL_SAMPLE`: fewer than 5 tasks; the normal approximation may be unreliable.
- `422` (`InvalidInput`): invalid task bounds, duplicate task `id`, empty task list, or a confidence level outside
  the open interval (0, 1).
- `413` (`InputTooLarge`): more than 50,000 tasks.

## Limitations

PERT relies on subjective three-point estimates and assumes independent task durations. The normal approximation is
less reliable with fewer than five tasks; simulation of remaining tasks is preferable when dependencies or skew
matter. Units are effort-days, not calendar days.
