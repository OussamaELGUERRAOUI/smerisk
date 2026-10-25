---
title: Value at Risk, Expected Shortfall and CFaR in Python
description: Compute a quantile, Value at Risk, Expected Shortfall and cash-flow at risk (CFaR) from a sample of simulated outcomes.
---

# Value at Risk, Expected Shortfall and CFaR in Python

Function: `risk_measures`

## Purpose

Summarizes the lower or upper tail of an existing sample using a quantile, Value at Risk (VaR), and Expected
Shortfall (ES). For example, a business can apply it to simulated ending cash balances to estimate downside risk.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

```python
from smerisk import risk_measures

result = risk_measures(samples=[float(value) for value in range(1, 101)], confidence=0.95)
print(round(result.result.quantile, 2))  # 5.95
print(round(result.result.var, 2))  # 44.55
print(result.result.expected_shortfall)  # 3.0
```

With these exact inputs, the sample mean is `50.5`; the linear empirical 5th percentile is `5.95`.

## HTTP

`POST /v1/risk/measures` takes the body below and returns the response envelope.

```json
{
  "samples": [
    560, 1123, 766, 1329, 972, 615, 1178, 800, 1363, 1006, 649, 1212,
    855, 1418, 1040, 683, 1246, 889, 1452, 1095, 738, 1280, 923, 566,
    1129, 772, 1335, 978, 600, 1163, 806, 1369, 1012, 655, 1218, 840,
    1403, 1046, 689, 1252, 895, 1458, 1080, 723, 1286, 929, 572, 1135,
    778, 1320, 963, 606, 1169, 812, 1375, 1018, 640, 1203, 846, 1409,
    1052, 695, 1258, 880, 1443, 1086, 729, 1292, 935, 578, 1120, 763,
    1326, 969, 612, 1175, 818, 1360, 1003, 646, 1209, 852, 1415, 1058,
    680, 1243, 886, 1449, 1092, 735, 1298, 920, 563, 1126, 769, 1332,
    975, 618, 1160, 803
  ],
  "confidence": 0.95,
  "orientation": "higher_is_better"
}
```

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `samples` | list of finite numbers | Yes | At least 100 results; each value uses the caller's unit |
| `confidence` | rate | No | Default `0.95` |
| `orientation` | `higher_is_better` or `lower_is_better` | No | Default `higher_is_better`; use the latter for costs |

## Calculation

For higher-is-better values, let `q` be the empirical `(1 - confidence)` quantile and `E[X]` the sample mean:

```text
VaR = E[X] - q
ES  = mean(X | X <= q)
```

For lower-is-better values, the corresponding upper tail is used:

```text
q = quantile(X, confidence)
VaR = q - E[X]
ES  = mean(X | X >= q)
```

Quantiles use NumPy's linear method. The standard deviation is the sample standard deviation (`ddof=1`).

Method reference: Rockafellar and Uryasev, “Conditional Value-at-Risk for General Loss Distributions,” *Journal of
Banking & Finance*, 2002.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `quantile` | Tail quantile corresponding to the selected orientation |
| `var` | Distance from the mean to that tail quantile |
| `expected_shortfall` | Mean of the observations in the selected tail |
| `mean` | Sample mean |
| `sd` | Sample standard deviation |

The envelope includes `confidence`, `orientation`, and `quantile_method` (`linear`) in `assumptions`.

## Warnings and errors

- No formula-specific warnings are currently emitted.
- `422`: invalid input or fewer than 100 observations.
- `413`: more than 50,000 observations.

## Limitations

These are empirical estimates and depend on the sample representing the relevant population. Tail estimates are
particularly uncertain for small samples; the function requires 100 observations but does not guarantee tail
precision.
