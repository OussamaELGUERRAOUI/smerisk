# `erlang_c`

## Purpose

Estimates waiting probability and staffing needs for a service queue with random arrivals and service times. It can
help a small business plan staffing for a phone line, checkout counter, or quick-service operation.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

A takeaway counter: 45 orders an hour, 4 minutes per order, target 80% served within 2 minutes:

```python
from smerisk import erlang_c

result = erlang_c(arrival_rate=45, service_time_minutes=4, target_wait_seconds=120, target_service_level=0.8)
print(result.result.offered_load)  # 3.0: three people busy on average
print(result.result.recommended_agents)  # 5
print(round(result.result.service_level, 3))  # 0.913
print(round(result.result.average_wait_seconds, 1))  # 28.3
print(
    [(row.agents, round(row.service_level, 2)) for row in result.result.table]
)  # [(4, 0.69), (5, 0.91), (6, 0.98), (7, 0.99)]
```

## HTTP

`POST /v1/operations/erlang-c` takes the body below and returns the response envelope.

```json
{
  "arrival_rate": 45,
  "service_time_minutes": 4,
  "agents": 4,
  "target_wait_seconds": 120
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `arrival_rate` | number > 0 | Yes | Arrivals per hour, λ |
| `service_time_minutes` | number > 0 | Yes | Mean service duration in minutes per agent |
| `agents` | integer ≥ 1 | No | Number of agents to evaluate |
| `target_wait_seconds` | number ≥ 0 | No | Target wait threshold in seconds |
| `target_service_level` | rate | No | Target share served before the wait threshold; requires a wait target to recommend staffing |

## Calculation

For mean service rate `μ = 60 / service_time_minutes` per agent and `c` agents, offered load is `A = λ/μ`. The
queue is stable only when `c > A`.

```text
P(wait) = ((A^c / c!) * c/(c-A)) /
          (sum(A^k/k! for k=0..c-1) + (A^c / c!) * c/(c-A))
W = P(wait) / (c*mu - lambda)
SL(t) = 1 - P(wait) * exp(-(c*mu - lambda)*t)
```

This is the M/M/c Erlang C queueing model. In `SL(t)`, `t` is in hours; convert the input wait threshold from
seconds before applying the formula. `P(wait)` is computed through the Erlang B recursion
`B(k) = A·B(k−1) / (k + A·B(k−1))`, `P(wait) = c·B(c) / (c − A·(1 − B(c)))`, which stays accurate for large
teams.

Reference: Gross, Shortle, Thompson, and Harris, *Fundamentals of Queueing Theory*, 5th ed., Wiley, 2018.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `offered_load` | `A`, the equivalent number of continuously busy agents |
| `agents` | Team size described by the next four fields: `agents` if given, else the recommendation, else the smallest stable team |
| `occupancy` | `A / c` |
| `p_wait` | Probability an arrival waits |
| `average_wait_seconds` | Mean waiting time in seconds |
| `service_level` | Probability of service within the target wait; `null` without `target_wait_seconds` |
| `recommended_agents` | Smallest agent count meeting the requested service level, when requested |
| `table[]` | The same metrics from the smallest stable team (`floor(A) + 1`) to two agents beyond the evaluated or recommended team |

## Warnings and errors

- `409` (`ComputationImpossible`, `UNSTABLE_QUEUE`): `agents` is not greater than the offered load; `details` gives
  the minimum stable team.
- `422` (`InvalidInput`): non-positive rates or durations, or `target_service_level` without `target_wait_seconds`.
- `413` (`InputTooLarge`): more than 10,000 agents needed or requested.

## Limitations

M/M/c assumes Poisson arrivals, independent exponentially distributed service times, identical agents, and a
steady-state queue. It does not capture abandonment, priority classes, breaks, or time-varying demand. These
assumptions can be poor for table service or highly variable service processes.
