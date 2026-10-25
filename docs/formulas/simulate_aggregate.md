---
title: Monte Carlo aggregate simulation in Python
description: Simulate signed uncertain components over several periods with correlations, and get the distribution of the total, for example a weekly cash balance.
---

# Monte Carlo aggregate simulation in Python

Function: `simulate_aggregate`

## Purpose

Monte Carlo simulation of signed uncertain components over a sequence of periods. Use it to estimate cash-flow or
balance distributions, such as a small business's projected weekly cash position.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

```python
from smerisk import simulate_aggregate

result = simulate_aggregate(
    periods=["2026-01", "2026-02"],
    components=[
        {
            "id": "receipts",
            "sign": 1,
            "by_period": {
                "2026-01": {"type": "normal", "mean": 100, "sd": 10},
                "2026-02": {"type": "normal", "mean": 100, "sd": 10},
            },
        },
        {
            "id": "payments",
            "sign": -1,
            "by_period": {
                "2026-01": {"type": "fixed", "value": 50},
                "2026-02": {"type": "fixed", "value": 50},
            },
        },
    ],
    n_simulations=10_000,
    seed=42,
)
print(result.result.horizon.cfar)
print(result.assumptions["seed"])
```

The seed makes the run reproducible for the same package version and input. Each ungrouped component and each correlation
group draws from its own random stream: adding or removing a component and rerunning with the same seed leaves the
other components' draws unchanged, so the difference between the two runs is the effect of that component alone.

## HTTP

`POST /v1/simulation/aggregate` takes the body below; above 20,000 draws it answers `202` with a job (see [jobs](../service.md#jobs-23)).

```json
{
  "periods": ["2026-01"],
  "components": [
    {
      "id": "receipts",
      "sign": 1,
      "by_period": {"2026-01": {"type": "normal", "mean": 1000, "sd": 100}}
    }
  ],
  "n_simulations": 10000,
  "seed": 42
}
```

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `periods` | ordered list of `YYYY-MM` months or `YYYY-Www` ISO weeks | Yes | At least one; do not mix month and week periods |
| `opening_balance` | number | No | Currency; default `0` |
| `components[].id` | non-empty string | Yes | Opaque component identifier |
| `components[].sign` | `1` or `-1` | Yes | `1` for receipts/income, `-1` for payments/costs |
| `components[].by_period` | period-to-distribution object | Yes | A missing period contributes `0` |
| `components[].correlation_group` | string or null | No | Components in the same group share a correlated draw |
| `components[].persistent` | boolean | No | Default `false`: a new draw each period. `true`: one draw for the whole horizon (a hire that happens for all its months or none). In a group, all components or none |
| `correlation` | Spearman correlation matrix | No | Independent groups by default |
| `n_simulations` | integer | No | `1,000`–`100,000`; default `10,000` |
| `seed` | non-negative integer | No | Generated and returned if omitted |
| `quantiles` | list of rates | No | Default `[0.05, 0.10, 0.50, 0.90, 0.95]` |
| `threshold` | number or null | No | Currency balance threshold |
| `confidence` | rate | No | Default `0.95` |
| `orientation` | `higher_is_better` or `lower_is_better` | No | Default `higher_is_better` (cash); `lower_is_better` for a cost, whose risk is an overrun |

Distributions are `fixed`, `normal`, `lognormal`, `triangular`, `pert`, `uniform`, `bernoulli`, `beta`, or `empirical`;
see the shared distribution contract in the API specification (§2.4).

## Calculation

For simulation `s` and period `t`:

```text
X[s,t] = sum(sign[c] * Y[c,s,t])
C[s,t] = opening_balance + sum(X[s,u] for u <= t)
```

Correlations are modeled with a Gaussian copula: correlate standard normal draws by Cholesky factorization, then
transform each draw through the inverse CDF of its component distribution. Missing correlations imply independence.
Strong dependence may understate joint tail risk.

By default each period draws again, so a component is independent from one period to the next. A persistent
component, or a group whose components are all persistent, draws once and keeps that draw in every period. Within a
persistent group, components can describe exclusive outcomes of one event: for an invoice paid on time in week 1 with
probability 0.6 or late in week 3 with probability 0.3, week 1 receives `bernoulli(0.6, A)`, and week 3 receives
`bernoulli(0.9, A)` plus `bernoulli(0.6, -A)` from a second component of the group.

Method reference: Glasserman, *Monte Carlo Methods in Financial Engineering*, Springer, 2004.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `by_period[].period` | Period label |
| `by_period[].flow_quantiles` | Requested quantiles of net flow |
| `by_period[].cumulative_quantiles` | Requested quantiles of ending cumulative balance |
| `by_period[].expected_flow` | Mean net flow |
| `by_period[].expected_cumulative` | Mean cumulative balance |
| `by_period[].p_below_threshold` | Probability of ending below `threshold`, or `null` when no threshold was given |
| `horizon.cfar` | Value at Risk of the ending balance, interpreted as CFaR: mean − quantile at 1 − confidence, or quantile at confidence − mean for `lower_is_better` |
| `horizon.expected_shortfall` | Mean ending balance in the worst tail (the low tail, or the high tail for `lower_is_better`) |
| `horizon.p_ever_below_threshold` | Probability of going below the threshold at least once, or `null` |
| `contributions[]` | Component variance share; shares sum to approximately 1 |

The common response envelope also returns used assumptions, warnings, and metadata.

## Warnings and errors

- `HIGH_UNCERTAINTY`: the 90% interval is wider than the central value.
- `CORRELATION_ADJUSTED`: a correlation matrix was adjusted slightly to be positive definite.
- `422`: invalid fields, duplicate component IDs, invalid period ordering, or inconsistent periods.
- `409`: correlation matrix cannot be adjusted within the accepted tolerance.
- `413`: more than 100,000 simulations, 200 components, or 60 periods.

## Limitations

The Gaussian copula captures ordinary dependence but may understate simultaneous extreme outcomes. The model assumes
that the supplied distributions and correlations represent the future. At most 100,000 simulations, 200 components,
and 60 periods are supported.
