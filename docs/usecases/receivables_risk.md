---
title: Receivables risk and expected credit loss in Python
description: Estimate how much of today's receivables will end unpaid from invoice ageing history, with a Markov chain and expected loss.
---

# Receivables risk and expected credit loss in Python

Function: `receivables_risk`

## Purpose

Estimates how much of today's receivables will end unpaid. Invoices move each month between ageing states (current,
1 to 30 days late, 31 to 60, more than 60, paid, defaulted); the observed moves form a Markov chain
([`markov_payments`](../formulas/markov_payments.md)) that gives the probability of default from each state, and
[`expected_loss`](../formulas/expected_loss.md) applies it to each open invoice.

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

80 fictitious invoices of 2,400 € over two years, some paid late, a few written off:

```python
import datetime as dt

from smerisk.usecases import receivables_risk

invoices = []
for i in range(80):
    issue = dt.date(2024, 11, 4) + dt.timedelta(days=9 * i)
    due = issue + dt.timedelta(days=30)
    days_late = [0, 0, 0, 12, 0, 35, 0, 70][i % 8]
    paid = due + dt.timedelta(days=days_late)
    invoice = {
        "invoice_ref": f"F{i:03d}",
        "customer_ref": f"C{i % 5}",
        "issue_date": issue.isoformat(),
        "due_date": due.isoformat(),
        "amount": 2_400.0,
    }
    if i % 19 == 7:
        invoice["written_off_date"] = (due + dt.timedelta(days=150)).isoformat()
    elif paid <= dt.date(2026, 10, 20):
        invoice["payments"] = [{"date": paid.isoformat(), "amount": 2_400.0}]
    invoices.append(invoice)
out = receivables_risk(invoices=invoices, settings={"as_of": "2026-10-20"})
p_default = out.result.ageing.absorption.p_default_from
print(
    {state: round(p, 3) for state, p in p_default.items()}
)  # {'CURRENT': 0.052, 'LATE_30': 0.164, 'LATE_60': 0.327, 'LATE_90': 0.6}
print(out.result.expected_loss.expected_loss)  # 2989.19
```

## HTTP

`POST /v1/usecases/receivables-risk` takes the body below and returns the response envelope.

```json
{"invoices": [
   {"invoice_ref": "F1", "customer_ref": "C1", "issue_date": "2026-07-01", "due_date": "2026-07-31",
    "amount": 2400, "payments": [{"date": "2026-09-10", "amount": 2400}]},
   {"invoice_ref": "F2", "customer_ref": "C2", "issue_date": "2026-08-01", "due_date": "2026-08-31",
    "amount": 1800},
   {"invoice_ref": "F3", "customer_ref": "C1", "issue_date": "2026-09-01", "due_date": "2026-10-01",
    "amount": 3000}],
 "settings": {"as_of": "2026-10-20", "lgd": 0.8}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `invoices` or `ledger_entries` | records | one of them | — | invoices, or a lettered FEC from which they are rebuilt |
| `settings.as_of` | date | no | today | later data is ignored |
| `settings.history_months` | months | no | 24 | month ends observed |
| `settings.horizon` | months | no | 6 | months projected |
| `settings.lgd` | rate | no | 1 | share of a defaulted amount that is lost |
| `settings.payment_terms_days` | days | no | 30 | FEC only: due date = issue date + days |
| `settings.n_simulations`, `seed`, `confidence` | — | no | no simulation | also simulate the loss distribution |
| `settings.overrides` | object | no | — | `transitions`, `current_exposure`, `lgd` |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `transitions` | state of each invoice at consecutive month ends, weighted by the amount still due |
| `current_exposure` | amount still due by state at `as_of` |
| `lgd` | setting |

From a FEC, an invoice is a debit on a customer account (411) for one document; it is paid when its lettering group
is complete, and defaulted when the account is cleared against 416 or 654 (see
[parameter preparation](../parameter-preparation.md)).

## Output

| Field | Meaning |
| --- | --- |
| `ageing` | result of `markov_payments`: transition matrix, monthly projection, default probability and delay by state |
| `expected_loss` | result of `expected_loss` over the open invoices, by customer (`by_segment`) |

## Warnings and data quality

- `data_quality.requirements`: 24 months of history.
- 422 when no invoice was open at two consecutive month ends, or none is open at `as_of`.

## Limitations

The chain assumes that an invoice's next state depends only on its current state, not on the customer or on how
long it has been in that state. A customer in difficulty is better followed individually.
