# `attrition`

## Purpose

Measures how long employees stay: the share still present after 3, 6, 12, and 24 months, by group if needed. An
HR manager can compare sites or contract types, and see when departures concentrate. The use case merges contracts,
decides which exits count, and calls [`kaplan_meier`](../formulas/kaplan_meier.md).

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

24 fictitious employees hired in January 2024: 5 resigned, 1 fixed-term contract ended (censored, not a departure):

```python
from smerisk.usecases import attrition

periods = [{"employee_ref": f"e{i}", "start": "2024-01-08"} for i in range(18)]
periods += [
    {"employee_ref": f"x{i}", "start": "2024-01-08", "end": end, "end_reason": "RESIGNATION"}
    for i, end in enumerate(["2024-03-29", "2024-06-28", "2024-11-29", "2025-05-30", "2025-09-30"])
]
periods += [{"employee_ref": "t1", "start": "2024-01-08", "end": "2024-06-30", "end_reason": "END_OF_FIXED_TERM"}]
out = attrition(employment_periods=periods, settings={"as_of": "2026-10-20"})
curve = out.result.curves[0]
print(curve.subjects, curve.events)  # 24 5
print(
    [(p.t, round(p.survival, 3)) for p in curve.at_time_points]
)  # [(3.0, 0.958), (6.0, 0.917), (12.0, 0.873), (24.0, 0.786)]
```

## HTTP

`POST /v1/usecases/attrition` takes the body below and returns the response envelope.

```json
{"employment_periods": [{"employee_ref": "e1", "start": "2024-01-08", "end": "2025-05-30",
                         "end_reason": "RESIGNATION", "groups": {"site_ref": "s1"}}],
 "settings": {"as_of": "2026-10-20", "group_by": "site_ref"}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `employment_periods` or `contracts` | records | one of them | — | periods, or contracts merged when at most one day apart |
| `settings.as_of` | date | no | today | periods still open, or ending later, are censored at this date |
| `settings.group_by` | `qualification`, `site_ref`, `contract_type` | no | none | one curve per group |
| `settings.time_points` | months | no | 3, 6, 12, 24 | months reported |
| `settings.exclude_reasons` | end reasons | no | `END_OF_FIXED_TERM`, `RETIREMENT` | exits censored rather than counted |
| `settings.confidence` | level | no | 0.95 | confidence limits |
| `settings.overrides` | object | no | — | `subjects` |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `subjects` | months of employment; event = exit for a reason not excluded |

An exit without a reason counts as `OTHER`, so as a departure.

## Output

The result of [`kaplan_meier`](../formulas/kaplan_meier.md): one curve for everyone (`ALL`) and one per group, with
confidence limits, the median duration, and the log-rank test between groups. Groups of fewer than 3 employees are
not returned (`suppressed_groups`).

## Warnings and data quality

- `data_quality.requirements`: 20 employees; below, each exit moves the curve by 5 points or more.
- 422 when no period starts before `as_of`, or when both `employment_periods` and `contracts` are sent.

## Limitations

Departures are assumed independent of the date of hire. A reorganization or a site closure is not a usual
departure: exclude its reason or analyse it apart.
