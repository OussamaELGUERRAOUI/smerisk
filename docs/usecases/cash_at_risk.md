---
title: Cash-flow at risk (CFaR) in Python
description: Forecast the weekly bank balance of the next quarter and the probability of running short from invoices, payments and known flows.
---

# Cash-flow at risk (CFaR) in Python

Function: `cash_at_risk`

## Purpose

Forecasts the bank balance week by week over the next quarter and the probability of running short. Receipts are
uncertain (a customer pays on time, late, or not within the quarter), recurring payments vary, and some flows are
already known. The use case prepares each of them and simulates the balance with
[`simulate_aggregate`](../formulas/simulate_aggregate.md).

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

A fictitious company with 15,000 € in the bank, three open invoices, and its payroll and VAT ahead:

```python
import datetime as dt

from smerisk.usecases import cash_at_risk

history = []
for i in range(60):
    issue = dt.date(2026, 1, 5) + dt.timedelta(days=4 * i)
    due = issue + dt.timedelta(days=30)
    paid = due + dt.timedelta(days=[0, 0, 2, 0, 18, 0, 45, 0, 3, 70][i % 10])
    history.append(
        {
            "invoice_ref": f"H{i}",
            "customer_ref": f"C{i % 4}",
            "issue_date": issue.isoformat(),
            "due_date": due.isoformat(),
            "amount": 3_000,
            "payments": [{"date": paid.isoformat(), "amount": 3_000}],
        }
    )
open_invoices = [
    {"invoice_ref": "O1", "customer_ref": "C0", "issue_date": "2026-10-01", "due_date": "2026-10-31", "amount": 12_000},
    {"invoice_ref": "O2", "customer_ref": "C1", "issue_date": "2026-10-05", "due_date": "2026-11-04", "amount": 9_000},
    {"invoice_ref": "O3", "customer_ref": "C2", "issue_date": "2026-08-20", "due_date": "2026-09-19", "amount": 6_000},
]
flows = [
    {"date": "2026-10-30", "amount": 21_000, "direction": "OUT", "category": "payroll"},
    {"date": "2026-11-15", "amount": 7_500, "direction": "OUT", "category": "tax"},
    {"date": "2026-11-30", "amount": 21_000, "direction": "OUT", "category": "payroll"},
]
out = cash_at_risk(
    bank_balances=[{"date": "2026-10-20", "amount": 15_000}],
    invoices=history + open_invoices,
    scheduled_flows=flows,
    settings={"as_of": "2026-10-20", "seed": 5},
)
print(out.result.by_period[0].period, out.result.by_period[-1].period)  # 2026-W43 2027-W02
print(out.result.by_period[-1].expected_cumulative)  # 1069.8
print(round(out.result.horizon.p_ever_below_threshold, 3))  # 0.531
```

The payroll of 30 October leaves before some customers pay: the balance goes below 0 at least once in 53% of the
scenarios.

## HTTP

`POST /v1/usecases/cash-at-risk` takes the body below and answers `202` with a job to poll (see [jobs](../service.md#jobs-23)); the job's result is the response envelope.

```json
{"bank_balances": [{"date": "2026-10-20", "amount": 15000}],
 "invoices": [{"invoice_ref": "O1", "customer_ref": "C0", "issue_date": "2026-10-01", "due_date": "2026-10-31",
               "amount": 12000, "customer_sector": "retail"}],
 "scheduled_flows": [{"date": "2026-10-30", "amount": 21000, "direction": "OUT", "category": "payroll"}],
 "settings": {"as_of": "2026-10-20", "horizon": 13, "threshold": 0}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `bank_balances` | records | yes | — | the latest date at or before `as_of` gives the opening balance (accounts summed) |
| `invoices` or `ledger_entries` | records | one of them | — | invoices, or a FEC: invoices rebuilt and bank entries read |
| `scheduled_flows` | records | no | — | known, dated flows |
| `settings.horizon` | weeks | no | 13 | ISO weeks from the week of the day after `as_of` |
| `settings.threshold` | euros | no | 0 | balance below which cash is short |
| `settings.history_months` | months | no | 12 | FEC history of recurring payments |
| `settings.sector_correlation` | correlation | no | 0.4 | between receipts of customers of the same sector |
| `settings.payment_terms_days` | days | no | 30 | FEC only: due date = issue date + days |
| `settings.n_simulations`, `seed`, `confidence`, `quantiles` | — | no | 10,000, drawn, 0.95, standard | simulation |
| `settings.overrides` | object | no | — | paths of the table below |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `receipts.on_time_probability` | share of invoices paid at most 5 days after their due date |
| `receipts.on_time_probability.<customer>` | the same per customer, shrunk toward the company |
| `receipts.late_delay_days` | days late of late payments, lognormal; median 30 and 90th percentile 90 with fewer than 5 |
| `recurring.<category>` | weekly totals of bank entries (512) by counterpart: `suppliers` (401), `payroll` (421), `social` (43), `tax` (44), `loans` (16), `other`, and `other_receipts` |

**Receipts.** An invoice not yet late is paid on time in the week of its due date, or late in the week of the median
delay if the late payment comes within the horizon, or not within the horizon. The three outcomes are exclusive: the
invoice is drawn once for the whole horizon. An invoice already late is paid in the week of the median remaining
delay, with the probability conditional on the delay already reached; beyond 99% of the observed delays it is not
expected (see [`receivables_risk`](receivables_risk.md)). Invoices are grouped by customer and week; beyond the
limit of 200 components, the smallest customers are pooled by week.

**Recurring flows.** Weeks without any flow count as 0. A category that also appears in `scheduled_flows` (for
example `payroll`) is not simulated: the known flows replace it.

## Output

The result of [`simulate_aggregate`](../formulas/simulate_aggregate.md): balance and flow quantiles by week, the
probability of being below `threshold` each week and at least once, the cash flow at risk at the horizon, and the
contribution of each component.

## Warnings and data quality

- `data_quality.requirements`: 26 weeks of FEC history when a FEC is sent.
- `data_quality.outliers`: the pooled customers, when the limit is reached.
- 422 without a balance at or before `as_of`, or without any customer invoice.

## Limitations

Recurring payments of different weeks are independent, so a monthly payment spread over weeks is approximate:
send it as a scheduled flow. Customers of different sectors are independent.
