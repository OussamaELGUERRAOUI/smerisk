---
title: Forecast reliability with Brier score and Kupiec test in Python
description: Check whether past probabilities and intervals deserve trust, using the Brier score and Kupiec's test on stored forecasts.
---

# Forecast reliability with Brier score and Kupiec test in Python

Function: `forecast_reliability`

## Purpose

Checks whether past forecasts deserve trust. Probabilities (a deal signed, an invoice paid) are scored with the Brier
score against the naive rate; intervals (a cash floor at 95%) are tested with Kupiec's test, which compares the
number of times the actual value fell outside with the number expected. The application stores the forecasts the
API returned and sends them back with what happened.

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

Eight fictitious signature probabilities, and a 95% cash floor checked over twelve months:

```python
from smerisk.usecases import forecast_reliability

pairs = [(0.9, 1), (0.8, 1), (0.7, 0), (0.6, 1), (0.3, 0), (0.2, 0), (0.4, 1), (0.1, 0)]
log = [
    {
        "forecast_ref": f"deal-{i}",
        "made_at": "2026-03-02",
        "target": "signature",
        "kind": "PROBABILITY",
        "p": p,
        "outcome": o,
    }
    for i, (p, o) in enumerate(pairs)
]
log += [
    {
        "forecast_ref": f"cash-{m}",
        "made_at": f"2025-{m:02d}-01",
        "target": "cash floor",
        "kind": "INTERVAL",
        "lower": 20_000,
        "level": 0.95,
        "outcome": 18_500 if m in (3, 8, 11) else 26_000,
    }
    for m in range(1, 13)
]
out = forecast_reliability(forecast_log=log, settings={"as_of": "2026-10-20"})
scores = out.result.probabilities
print(round(scores.brier_score, 4), round(scores.skill_score, 3))  # 0.15 0.4
interval = out.result.intervals[0]
print(interval.exceedances, interval.observations, interval.test.verdict)  # 3 12 TOO_MANY_EXCEEDANCES
print([w.code.value for w in out.warnings])  # ['SMALL_SAMPLE']: 12 months, 24 recommended
```

The floor was crossed 3 times in 12 months where 0.6 was expected: the cash risk was underestimated.

## HTTP

`POST /v1/usecases/forecast-reliability` takes the body below and returns the response envelope.

```json
{"forecast_log": [
   {"forecast_ref": "deal-1", "made_at": "2026-03-02", "target": "signature", "kind": "PROBABILITY", "p": 0.9,
    "outcome": 1},
   {"forecast_ref": "deal-2", "made_at": "2026-03-02", "target": "signature", "kind": "PROBABILITY", "p": 0.3,
    "outcome": 0},
   {"forecast_ref": "cash-1", "made_at": "2026-01-01", "target": "cash floor", "kind": "INTERVAL",
    "lower": 20000, "level": 0.95, "outcome": 26000}],
 "settings": {"as_of": "2026-10-20"}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `forecast_log` | records | yes | — | past forecasts; those without an outcome are ignored |
| `settings.as_of` | date | no | today | forecasts made later are ignored |
| `settings.n_bins` | integer | no | 10 | calibration bins |
| `settings.overrides` | object | no | — | `probabilities.forecasts`, `intervals.<level>.exceedances` |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `probabilities.forecasts` | `PROBABILITY` forecasts with an outcome 0 or 1 |
| `intervals.<level>.exceedances` | `INTERVAL` forecasts at that level whose actual value fell outside |

## Output

| Field | Meaning |
| --- | --- |
| `probabilities` | result of [`brier`](../formulas/brier.md), or `null` without resolved probabilities |
| `intervals[]` | one entry per level: `level`, `exceedances`, `observations`, and the [`kupiec`](../formulas/kupiec.md) `test` |

## Warnings and data quality

- `data_quality.requirements`: 30 resolved probabilities, and 24 intervals per level.
- 422 when no forecast has a known outcome. Warnings come from the formulas, prefixed with the level for intervals.

## Limitations

Kupiec's test only counts exceedances: it does not see whether they come in clusters. Forecasts of different
horizons or targets should be assessed separately.
