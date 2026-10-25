---
title: Fixed-price project risk with Monte Carlo in Python
description: Estimate the final cost, margin and the chances of staying within budget and deadline for fixed-price projects from timesheets.
---

# Fixed-price project risk with Monte Carlo in Python

Function: `fixed_price_risk`

## Purpose

For each fixed-price project, estimates the final cost, the margin, and the chances of staying within budget and of
finishing before the deadline. The use case computes the cost already spent from timesheets, the daily cost of the
team from consultants' costs, and simulates the remaining work with
[`fixed_price_completion`](../formulas/fixed_price_completion.md).

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

A fictitious website project of 60,000 €, three tasks left, one risk, two consultants:

```python
from smerisk.usecases import fixed_price_risk

project = {
    "project_ref": "website",
    "price": 60_000,
    "cost_budget": 40_000,
    "deadline": "2026-12-11",
    "remaining_tasks": [
        {"task_ref": "design", "optimistic": 10, "most_likely": 15, "pessimistic": 25},
        {"task_ref": "build", "optimistic": 30, "most_likely": 40, "pessimistic": 65},
        {"task_ref": "tests", "optimistic": 5, "most_likely": 8, "pessimistic": 14},
    ],
    "risks": [{"risk_ref": "api-change", "probability": 0.3, "impact_days_most_likely": 3, "impact_days_max": 8}],
    "team": ["k1", "k2"],
}
consultants = [{"consultant_ref": "k1", "daily_cost": 450}, {"consultant_ref": "k2", "daily_cost": 550}]
timesheets = [{"consultant_ref": "k1", "period": "2026-09", "mission_ref": "website", "billable_days": 12}]
out = fixed_price_risk(
    fixed_price_projects=[project],
    consultants=consultants,
    timesheets=timesheets,
    settings={"as_of": "2026-10-20", "seed": 42},
)
completion = out.result.projects[0].completion
print(out.derived_parameters["projects.website.actual_cost"].value)  # 5400.0
print(round(completion.p_within_budget, 3), round(completion.p_on_time, 3))  # 0.579 0.856
print(completion.margin_quantiles["0.5"])  # 21051.63
```

## HTTP

`POST /v1/usecases/fixed-price-risk` takes the body below and answers `202` with a job to poll (see [jobs](../service.md#jobs-23)); the job's result is the response envelope.

```json
{"fixed_price_projects": [{"project_ref": "website", "price": 60000, "cost_budget": 40000,
                           "deadline": "2026-12-11", "team": ["k1", "k2"],
                           "remaining_tasks": [{"task_ref": "build", "optimistic": 30, "most_likely": 40,
                                                "pessimistic": 65}]}],
 "consultants": [{"consultant_ref": "k1", "daily_cost": 450}, {"consultant_ref": "k2", "daily_cost": 550}],
 "settings": {"as_of": "2026-10-20", "seed": 42}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `fixed_price_projects` | records | yes | — | price, budget, deadline, remaining tasks, risks, team |
| `consultants` | records | yes | — | daily cost of each consultant |
| `timesheets` | records | no | — | days spent (`mission_ref` = `project_ref`); otherwise `actual_days` |
| `settings.as_of` | date | no | today | timesheets after its month are ignored |
| `settings.n_simulations`, `seed`, `quantiles` | — | no | 10,000, drawn, standard | simulation |
| `settings.overrides` | object | no | — | `projects.<ref>.daily_cost`, `.actual_cost`, `.remaining_tasks`, `.risks` |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `projects.<ref>.actual_cost` | timesheet days × each consultant's daily cost; else `actual_days` × team mean |
| `projects.<ref>.daily_cost` | team mean daily cost, weighted by planned days, `normal` with the spread between members |
| `projects.<ref>.remaining_tasks` | tasks with three estimates (days) |
| `projects.<ref>.risks` | register; impact PERT(0, most likely, max); a task with three equal estimates becomes a certain risk |

The deadline is counted in working days after `as_of`, with one day of effort per working day for each team member
with a known cost.

## Output

`projects[]`: `project_ref`, `deadline_days` (`null` once the deadline has passed), and the
[`fixed_price_completion`](../formulas/fixed_price_completion.md) `completion`: final cost and margin quantiles,
probabilities of a loss, of staying within budget, and of finishing on time, and the contribution of each task and
risk.

## Warnings and data quality

- `data_quality.requirements`: a known cost for each team member, and a cost already spent (timesheets or
  `actual_days`).
- 422 when no project has a team member with a known cost. Warnings come from the formula, prefixed with the project.

## Limitations

Tasks are independent and the team works on the project full time. A project late because people are on other
missions needs a lower capacity, set through `overrides` on the tasks or by changing the team.
