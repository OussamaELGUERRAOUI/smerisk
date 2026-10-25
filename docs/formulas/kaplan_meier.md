---
title: Kaplan-Meier survival curve in Python
description: Estimate survival probabilities from right-censored durations, with Greenwood confidence limits and a log-rank test between groups.
---

# Kaplan-Meier survival curve in Python

Function: `kaplan_meier`

## Purpose

Estimates the probability that an event has not yet occurred by a given time, while correctly handling observations
that are right-censored. Examples include employee retention, project duration, and customer tenure.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Employee retention in two teams of a restaurant (fictitious data):

```python
from smerisk import kaplan_meier

# Months of tenure; event=True: the employee left, event=False: still employed at the analysis date.
kitchen = [
    (2, True),
    (3, True),
    (5, True),
    (6, False),
    (8, True),
    (11, True),
    (14, False),
    (20, True),
    (26, False),
    (30, False),
]
floor = [
    (4, False),
    (9, True),
    (13, False),
    (18, True),
    (24, False),
    (28, False),
    (31, True),
    (35, False),
    (36, False),
    (40, False),
]
result = kaplan_meier(
    subjects=[{"duration": t, "event": e, "group": "kitchen"} for t, e in kitchen]
    + [{"duration": t, "event": e, "group": "floor"} for t, e in floor],
    time_points=[6, 12, 24],
)
kitchen_curve = result.result.curves[2]  # curves: ALL, then groups in alphabetical order
print(kitchen_curve.group)  # kitchen
print([round(p.survival, 3) for p in kitchen_curve.at_time_points])  # [0.7, 0.467, 0.311]
print(kitchen_curve.median)  # 11.0: half of the kitchen hires have left after 11 months
print(round(result.result.log_rank.p_value, 3))  # 0.043: the two teams' curves differ
print([w.code.value for w in result.warnings])  # ['SMALL_SAMPLE', 'SMALL_SAMPLE']: 10 subjects per team
```

The results are checked against SciPy (`scipy.stats.ecdf` with log-log intervals, `scipy.stats.logrank`) and the
classic Freireich leukemia data in the test suite.

## HTTP

`POST /v1/survival/kaplan-meier` takes the body below and returns the response envelope.

```json
{
  "subjects": [
    {"duration": 6, "event": true},
    {"duration": 12, "event": false}
  ],
  "time_points": [3, 6, 12],
  "confidence": 0.95
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `subjects[].duration` | number ≥ 0 | Yes | Observed duration in months |
| `subjects[].event` | boolean | Yes | `true` if event observed; `false` if right-censored |
| `subjects[].group` | string | No | Optional comparison group; `ALL` is reserved |
| `min_group_size` | integer ≥ 1 | No | Default `3`; smaller groups are suppressed (the `ALL` curve is always returned) |
| `time_points` | list of durations ≥ 0 | No | Months; default `[3, 6, 12, 24, 36]` |
| `confidence` | rate | No | Default `0.95` |

## Calculation

At each event time `t[i]`, let `d[i]` be the number of events and `n[i]` the number at risk immediately before
that time:

```text
S_hat(t) = product(1 - d[i] / n[i]) for t[i] <= t
Var(S_hat(t)) = S_hat(t)^2 * sum(d[i] / (n[i] * (n[i] - d[i]))) for t[i] <= t
```

This is the Kaplan-Meier product-limit estimator with Greenwood variance. Confidence limits use the log-minus-log
transformation, `S^exp(±z·se)` with `se = sqrt(sum(...)) / |ln S|`, to stay within [0, 1]; they are `null` once
survival reaches 0. Subjects censored at an event time are counted at risk for that event. The log-rank test
compares the returned groups (suppressed groups are excluded) when there are at least two.

References: Kaplan and Meier, “Nonparametric Estimation from Incomplete Observations,” *Journal of the American
Statistical Association*, 1958; Greenwood, *Report on the Natural Duration of Cancer*, 1926.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `curves[].group` | Group name, or `ALL` for the full sample; `ALL` first, then groups in alphabetical order |
| `curves[].subjects`, `curves[].events` | Number of subjects and of observed events |
| `curves[].steps[]` | Step points with `t`, `survival`, `lower`, `upper`, `at_risk`, and `events` |
| `curves[].at_time_points` | `t`, `survival`, `lower`, `upper` at each requested duration; `null` beyond the longest observed duration |
| `curves[].median` | First event time where estimated survival is at most 0.5, or `null` if not reached |
| `suppressed_groups` | Groups smaller than `min_group_size`, not returned |
| `log_rank` | `groups`, chi-square `statistic`, `df`, and `p_value` when at least two groups are returned |

## Warnings and errors

- `SMALL_SAMPLE`: fewer than 20 subjects or fewer than 5 events in a returned curve.
- `EXTRAPOLATION`: a requested time point is beyond the longest observed duration.
- `422` (`InvalidInput`): negative durations or time points, an empty subject list, a group named `ALL`, or a
  confidence level outside (0, 1).
- `413` (`InputTooLarge`): more than 50,000 subjects.

## Limitations

The standard estimator assumes non-informative right censoring: censoring should not depend on an unobserved event
time after accounting for the analysis. Group comparisons are observational, not causal. Small groups are suppressed
to reduce disclosure risk.
