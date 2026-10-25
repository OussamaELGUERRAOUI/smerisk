---
title: Expected credit loss of receivables in Python
description: Compute the expected credit loss of a receivables portfolio by segment, with optional Monte Carlo loss quantiles and Expected Shortfall.
---

# Expected credit loss of receivables in Python

Function: `expected_loss`

## Purpose

Calculates expected credit loss for a portfolio of receivables and optionally simulates the total-loss distribution.
A business can use the result as an input to provisioning and credit-risk review.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

```python
from smerisk import expected_loss

result = expected_loss(
    exposures=[
        {"id": "customer-1", "ead": 10_000, "pd": 0.02, "lgd": 0.6, "segment": "trade"},
        {"id": "customer-2", "ead": 5_000, "pd": 0.10, "segment": "trade"},
        {"id": "customer-3", "ead": 2_000, "pd": {"type": "beta", "alpha": 2, "beta": 8}, "segment": "retail"},
    ],
    n_simulations=10_000,
    seed=42,
)
print(result.result.expected_loss)  # 1020.0: suggested provision
print(result.result.by_segment)  # {'retail': 400.0, 'trade': 620.0}
print(result.result.loss_quantiles["0.95"])  # 5000.0: one default of customer-2 is enough
print(result.result.expected_shortfall)  # 5631.21
print([w.code.value for w in result.warnings])  # ['HIGH_UNCERTAINTY']
```

Without `n_simulations`, only the exact expectation is computed. A Beta PD (for example a `beta_binomial`
posterior) enters the expectation through its mean and is drawn once per simulation. Each exposure draws from its
own random stream.

## HTTP

`POST /v1/credit/expected-loss` takes the body below and returns the response envelope.

```json
{
  "exposures": [
    {"id": "invoice-1", "ead": 1000, "pd": 0.05, "lgd": 0.8, "segment": "trade"}
  ],
  "n_simulations": 10000,
  "seed": 42
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `exposures[].id` | string | Yes | Opaque exposure or customer identifier |
| `exposures[].ead` | number ≥ 0 | Yes | Exposure at default, euros |
| `exposures[].pd` | rate or Beta distribution | Yes | Probability of default over the stated horizon; Beta as `{"type": "beta", "alpha": …, "beta": …}` |
| `exposures[].lgd` | rate | No | Loss given default, from 0 to 1; default `1` |
| `exposures[].segment` | string | No | Optional aggregation segment; missing segments are grouped as `UNASSIGNED` |
| `n_simulations` | integer | No | If supplied (1,000 to 100,000), simulate total losses |
| `seed` | non-negative integer | No | Required only for reproducible randomized results; generated if omitted |
| `quantiles` | list of levels | No | Simulated loss quantiles; default `[0.05, 0.10, 0.50, 0.90, 0.95]` |
| `confidence` | level | No | Level of `expected_shortfall`; default `0.95` |

## Calculation

Expected loss is:

```text
EL = sum(PD[i] * LGD[i] * EAD[i])
```

When simulation is requested, each exposure defaults independently according to its PD; a Beta PD is sampled from
its specified distribution. Portfolio loss quantiles and tail loss are estimated from the simulated aggregate.

References: IFRS Foundation, *IFRS 9 Financial Instruments*; FASB, *Accounting Standards Codification Topic 326*,
Current Expected Credit Losses (CECL).

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `expected_loss` | Total expected loss, euros |
| `by_segment` | Expected loss grouped by segment |
| `loss_quantiles` | Simulated aggregate-loss quantiles, when simulation is enabled |
| `expected_shortfall` | Mean total loss in the worst `1 − confidence` of simulations, when simulation is enabled |
| `concentration` | Share of expected loss carried by the five largest expected losses |

## Warnings and errors

- `HIGH_UNCERTAINTY`: the simulated 90% loss interval is wider than the median loss.
- `422` (`InvalidInput`): negative `ead`, a PD or LGD outside [0, 1], an invalid Beta, duplicate ids, or fewer than
  1,000 simulations.
- `413` (`InputTooLarge`): more than 50,000 exposures or 100,000 simulations.

## Limitations

The analytic expectation does not model dependence between defaults. The optional simulation explicitly assumes
independent defaults as specified; correlated defaults may materially increase portfolio tail risk. This result is
not, by itself, a complete IFRS 9 or CECL allowance calculation.
