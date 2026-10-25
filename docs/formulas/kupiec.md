---
title: Kupiec backtest of VaR exceedances in Python
description: Test whether the observed number of threshold exceedances is consistent with the expected rate, using Kupiec's likelihood-ratio test.
---

# Kupiec backtest of VaR exceedances in Python

Function: `kupiec`

## Purpose

Tests whether the observed frequency of risk-threshold exceedances is consistent with the expected rate. A business
can use it to backtest a VaR or cash-flow risk threshold over historical periods.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

A weekly cash floor announced at 95%: over one year, the balance went below it in 4 weeks out of 52.

```python
from smerisk import kupiec

result = kupiec(exceedances=4, observations=52, expected_rate=0.05)
print(round(result.result.observed_rate, 3))  # 0.077
print(round(result.result.p_value, 3))  # 0.407
print(result.result.verdict)  # OK: 4 crossings are compatible with 5%
```

## HTTP

`POST /v1/validation/kupiec` takes the body below and returns the response envelope.

```json
{
  "exceedances": 4,
  "observations": 52,
  "expected_rate": 0.05
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `exceedances` | integer | Yes | Number of periods exceeding the stated threshold, `x` |
| `observations` | integer | Yes | Number of tested periods, `T` |
| `expected_rate` | rate | Yes | Expected exceedance probability, for example `0.05` for 95% confidence |

Valid counts satisfy `0 ≤ exceedances ≤ observations`; expected rate must be strictly between 0 and 1.

## Calculation

The Kupiec proportion-of-failures likelihood-ratio test compares the expected exceedance probability `p` with the
observed rate `x/T`:

```text
LR = -2 * log((1-p)^(T-x) * p^x)
     + 2 * log((1-x/T)^(T-x) * (x/T)^x)
LR ~ chi-square(1)
```

The likelihood uses the limiting convention `0 * log(0) = 0` at boundary counts.

Reference: Kupiec, “Techniques for Verifying the Accuracy of Risk Measurement Models,” *The Journal of Derivatives*,
1995.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `observed_rate` | `exceedances / observations` |
| `lr` | Likelihood-ratio statistic |
| `p_value` | Upper-tail probability under the chi-square(1) approximation |
| `verdict` | `OK`, `TOO_MANY_EXCEEDANCES`, or `TOO_FEW_EXCEEDANCES` |

The specified verdict is `OK` when the p-value is at least 0.05; otherwise it distinguishes too many from too few
exceedances.

## Warnings and errors

- `SMALL_SAMPLE`: fewer than 24 observations; the test has low power.
- `422` (`InvalidInput`): negative exceedances, more exceedances than observations, no observations, or an expected
  rate outside the open interval (0, 1).

## Limitations

This is an unconditional coverage test: it evaluates exceedance frequency, not whether exceedances cluster or
whether risk estimates are conditionally calibrated. The chi-square approximation can be unreliable for short
samples or extreme expected rates.
