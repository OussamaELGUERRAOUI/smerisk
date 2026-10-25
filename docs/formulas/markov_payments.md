# `markov_payments`

## Purpose

Models movement of receivables between overdue, paid, and default states to forecast collections and ultimate loss.
A small business can use historical invoice transitions to estimate how much cash may arrive and when.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Month-end ageing of invoices observed over the past year, counted in invoices:

```python
from smerisk import markov_payments

result = markov_payments(
    transitions=[
        {"from": "CURRENT", "to": "CURRENT", "count": 40},
        {"from": "CURRENT", "to": "PAID", "count": 50},
        {"from": "CURRENT", "to": "LATE_30", "count": 10},
        {"from": "LATE_30", "to": "PAID", "count": 6},
        {"from": "LATE_30", "to": "LATE_60", "count": 4},
        {"from": "LATE_60", "to": "PAID", "count": 2},
        {"from": "LATE_60", "to": "LATE_90", "count": 2},
        {"from": "LATE_90", "to": "PAID", "count": 1},
        {"from": "LATE_90", "to": "DEFAULT", "count": 1},
    ],
    current_exposure={"CURRENT": 10_000, "LATE_30": 2_000, "LATE_90": 500},
    horizon_periods=3,
)
print([m.collections for m in result.result.projection])  # [6052.91, 2898.33, 1390.71]
print(
    {s: round(p, 3) for s, p in result.result.absorption.p_default_from.items()}
)  # {'CURRENT': 0.037, 'LATE_30': 0.142, 'LATE_60': 0.237, 'LATE_90': 0.379}
print(round(result.result.absorption.expected_months_to_payment["CURRENT"], 2))  # 1.95
print(result.result.expected_default_amount)  # 840.95
```

`p_default_from` can be used as the PD of each ageing bucket in `expected_loss`.

## HTTP

`POST /v1/credit/markov-payments` takes the body below and returns the response envelope.

```json
{
  "transitions": [
    {"from": "CURRENT", "to": "PAID", "count": 15},
    {"from": "CURRENT", "to": "LATE_30", "count": 5}
  ],
  "current_exposure": {"CURRENT": 12000, "LATE_30": 3000},
  "horizon_periods": 6,
  "smoothing": 0.5
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `states` | list of unique state names | No | Default `CURRENT`, `LATE_30`, `LATE_60`, `LATE_90`, `PAID`, `DEFAULT`; must include `PAID` and `DEFAULT`, which are absorbing, and at least one other state |
| `transitions[]` | `{from, to, count}` objects | Yes | Observed month-to-month transitions; `count` ≥ 0, may be an amount; repeated pairs are added; none may leave `PAID` or `DEFAULT` |
| `weight_by` | `count` or `amount` | No | Default `count` |
| `current_exposure` | state-to-number object | Yes | Current amounts (≥ 0) in non-absorbing states, euros; missing states count as 0 |
| `horizon_periods` | integer | No | Months; default `6`, at most 60 |
| `smoothing` | number ≥ 0 | No | Additive transition smoothing λ; default `0.5` |

## Calculation

Estimate each transition probability with additive smoothing:

```text
P[i,j] = (n[i,j] + lambda) / sum_k(n[i,k] + lambda)
distribution[t+1] = distribution[t] * P
```

For transient-to-transient submatrix `Q` and transient-to-absorbing submatrix `R`:

```text
N = (I - Q)^(-1)
B = N * R
```

`B` gives eventual absorption probabilities; `N` gives expected transient-state visits. The method is a finite-state
Markov chain with absorbing states. Smoothing adds λ toward every state from every non-absorbing state.

Expected months until payment are conditional on the receivable ending up paid,
`E[T | paid] = (N · b_paid)[i] / b_paid[i]`; expected months until resolution (paid or defaulted) are `N · 1`.

Reference: Norris, *Markov Chains*, Cambridge University Press, 1997.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `transition_matrix` | Estimated transition matrix `P` |
| `projection[]` | `month`, expected `balances` by state (PAID and DEFAULT cumulative), `collections`, and `new_defaults` |
| `absorption.p_default_from[state]` | Eventual default probability from each transient state |
| `absorption.p_paid_from[state]` | Eventual payment probability from each transient state |
| `absorption.expected_months_to_payment[state]` | Expected months until payment, for receivables that end up paid; `null` if payment is impossible |
| `absorption.expected_months_to_resolution[state]` | Expected months until paid or defaulted |
| `expected_default_amount` | Sum of exposure times state-specific default probability, euros |

## Warnings and errors

- `422` (`InvalidInput`): unknown states, duplicate states, `PAID` or `DEFAULT` missing, a transition leaving an
  absorbing state, negative counts, or exposure that is negative or held in an absorbing state.
- `409` (`ComputationImpossible`): `NO_TRANSITIONS_FROM_STATE` when a state has no observed transition and
  `smoothing` is 0; `NO_ABSORPTION` when some state can never reach `PAID` or `DEFAULT`.
- `413` (`InputTooLarge`): more than 60 months or 50,000 transitions.

## Limitations

The Markov assumption makes next-period state depend only on the current state. It assumes historical transitions
remain relevant and that the selected states capture important differences between invoices. Results are sensitive
to sparse transition data and the chosen smoothing constant.
