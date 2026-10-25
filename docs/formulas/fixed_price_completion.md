---
title: Monte Carlo completion of a fixed-price project in Python
description: Simulate the final cost, margin and deadline of an in-progress fixed-price project from remaining tasks and identified risks.
---

# Monte Carlo completion of a fixed-price project in Python

Function: `fixed_price_completion`

## Purpose

Simulates final cost, margin, and schedule risk for an in-progress fixed-price project. A small contractor can use
the output to assess the chance that remaining work and identified risks will erase the expected margin.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

```python
from smerisk import fixed_price_completion

result = fixed_price_completion(
    contract_price=12_000,
    actual_cost=3_000,
    cost_budget=9_000,
    remaining_tasks=[
        {"id": "implementation", "optimistic": 5, "most_likely": 8, "pessimistic": 15},
        {"id": "testing", "optimistic": 2, "most_likely": 3, "pessimistic": 6},
    ],
    daily_cost={"type": "fixed", "value": 500},
    risks=[
        {
            "id": "client_delay",
            "probability": 0.3,
            "impact_days": {"type": "triangular", "min": 1, "mode": 2, "max": 5},
        }
    ],
    deadline_days=15,
    seed=42,
)
print(result.result.margin_quantiles["0.5"])  # 2716.18
print(result.result.p_loss)  # 0.0208
print(result.result.p_within_budget)  # 0.4062
print(result.result.p_on_time)  # 0.8225: one person, 15 workdays
print(
    {c.id: round(c.share, 2) for c in result.result.risk_contributions}
)  # {'implementation': 0.6, 'testing': 0.09, 'client_delay': 0.31}
print([w.code.value for w in result.warnings])  # ['HIGH_UNCERTAINTY']: 90% margin interval wider than the median
```

The seed makes the run reproducible for the same package version and input. Each task, each risk, and the daily
cost draw from their own random stream: removing `client_delay` and rerunning with the same seed leaves the task
draws unchanged, so the difference between the two runs is the effect of that risk alone.

## HTTP

`POST /v1/projects/fixed-price-completion` takes the body below and answers `202` with a job to poll (see [jobs](../service.md#jobs-23)); the job's result is the response envelope.

```json
{
  "contract_price": 12000,
  "actual_cost": 3000,
  "remaining_tasks": [
    {"id": "implementation", "optimistic": 5, "most_likely": 8, "pessimistic": 15}
  ],
  "daily_cost": {"type": "fixed", "value": 500},
  "n_simulations": 10000,
  "seed": 42
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `contract_price` | number > 0 | Yes | Contract price, euros excluding VAT |
| `actual_cost` | number ≥ 0 | Yes | Cost incurred to date, euros |
| `cost_budget` | number | No | Cost budget in euros; used for `p_within_budget` |
| `remaining_tasks` | list of PERT tasks | Yes | Each has `id`, `optimistic`, `most_likely`, `pessimistic` in days |
| `daily_cost` | distribution | Yes | Cost per workday, euros |
| `risks[]` | list | No | `id`, occurrence `probability` (0–1), and `impact_days` distribution |
| `deadline_days` | number | No | Available workdays before deadline |
| `team_capacity_days_per_period` | number > 0 | No | Effort days the team delivers per workday; requires `deadline_days`; default 1 |
| `n_simulations` | integer | No | As §3.1; default 10,000 |
| `seed` | non-negative integer | No | Generated and returned if omitted |
| `quantiles` | list of levels | No | Default `[0.05, 0.10, 0.50, 0.90, 0.95]`, as §3.1 |

## Calculation

Remaining task duration uses a scaled Beta-PERT distribution. Its shape parameters are
`alpha = 1 + 4(m-a)/(b-a)` and `beta = 1 + 4(b-m)/(b-a)`. Each risk is sampled as a Bernoulli event; an occurring
risk adds its sampled impact days.

For each simulation:

```text
EAC    = actual_cost + (sum(task_days) + sum(risk_occurs * risk_impact_days)) * daily_cost
margin = contract_price - EAC
```

A single daily cost is drawn per simulation and applies to all remaining effort. When a deadline is supplied, the
project is on time when simulated remaining effort (task days plus risk days) is at most
`deadline_days × team_capacity_days_per_period`.

Contributions use the Euler allocation `Cov(item cost, remaining cost) / Var(remaining cost)`, where an item's cost
is its days times the drawn daily cost. Margin variance equals remaining-cost variance, so shares sum to 1; the
daily-cost uncertainty is spread across items in proportion to their days.

Reference: Vose, *Risk Analysis: A Quantitative Guide*, 3rd ed., Wiley, 2008 (PERT distributions and Monte Carlo
risk analysis).

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `eac_quantiles` | Quantiles of Estimate at Completion, in euros (rounded to the cent) |
| `margin_quantiles` | Quantiles of final margin, in euros |
| `margin_rate_quantiles` | Quantiles of margin divided by contract price |
| `p_loss` | Probability that final margin is negative |
| `p_within_budget` | Probability final cost is within `cost_budget`, if supplied |
| `p_on_time` | Probability of finishing within `deadline_days`, if supplied |
| `risk_contributions[]` | `id`, `kind` (`task` or `risk`), and `share` of margin variance; shares sum to 1 |

## Warnings and errors

- `HIGH_UNCERTAINTY`: the 90% margin interval is wider than the absolute median margin.
- `422` (`InvalidInput`): invalid task bounds, probabilities, or distributions; non-positive `contract_price`;
  negative `actual_cost`; `team_capacity_days_per_period` without `deadline_days`; an `id` shared by two tasks, two
  risks, or a task and a risk.
- `413` (`InputTooLarge`): more than 100,000 simulations, or more than 200 tasks and risks combined.

## Limitations

The model depends on the quality of task ranges, risk probabilities, impact estimates, and daily-cost distribution.
It does not model task dependencies unless represented by the supplied inputs. The specified risk contributions
explain variance; they do not identify causal drivers.
