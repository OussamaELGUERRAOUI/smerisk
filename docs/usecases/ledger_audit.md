---
title: Benford's law audit of FEC accounting entries in Python
description: Test purchase and expense amounts of a FEC ledger export against Benford's law to find accounts worth a closer look.
---

# Benford's law audit of FEC accounting entries in Python

Function: `ledger_audit`

## Purpose

Tests whether the amounts booked on purchase and expense accounts follow Benford's law. A set of amounts that does
not, such as many invoices just below an approval threshold, is worth a closer look by the accountant or auditor.
The use case reads the ledger as exported (FEC), selects the accounts, and calls [`benford`](../formulas/benford.md).

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

800 fictitious purchase lines on account 606100:

```python
import numpy as np

from smerisk.usecases import ledger_audit

rng = np.random.default_rng(2)
entries = [
    {
        "JournalCode": "AC",
        "EcritureNum": str(n),
        "EcritureDate": "20260315",
        "CompteNum": "606100",
        "Debit": round(float(amount), 2),
        "Credit": 0,
    }
    for n, amount in enumerate(10 ** rng.uniform(1.5, 4.5, 800))
]
out = ledger_audit(ledger_entries=entries, settings={"as_of": "2026-10-20"})
print(out.result.n_used, out.result.conformity)  # 800 ACCEPTABLE
print(out.result.suspicious_digits)  # []
print(out.data_quality.usable)  # True
```

A FEC file can be read first with `smerisk.contracts.read_fec(path).records` and passed as `ledger_entries`.

## HTTP

`POST /v1/usecases/ledger-audit` takes the body below and returns the response envelope.

```json
{"ledger_entries": [{"JournalCode": "AC", "EcritureNum": "1", "EcritureDate": "20260315", "CompteNum": "606100",
                     "Debit": "1234,56", "Credit": "0,00"}],
 "settings": {"as_of": "2026-10-20", "accounts": ["6", "2"]}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `ledger_entries` | FEC lines | yes | — | [raw data contract](../data-contracts.md); labels are dropped |
| `settings.as_of` | date | no | today | later entries are ignored |
| `settings.accounts` | list of prefixes | no | `["6", "2"]` | accounts analysed |
| `settings.min_amount` | euros | no | 10 | smaller amounts are left out of the test |
| `settings.test` | `first_digit`, `first_two_digits` | no | `first_digit` | digits tested |
| `settings.overrides` | object | no | — | `amounts` |

## Preparation

Each line on a selected account gives one amount: the absolute value of debit − credit. The label returned for a
suspicious amount is `JournalCode-EcritureNum`, never a name.

| Derived parameter | Source |
| --- | --- |
| `amounts` | lines of the selected accounts up to `as_of` |

## Output

The result of [`benford`](../formulas/benford.md): observed and expected frequencies, MAD and conformity, chi-square,
suspicious digits, and the labels of the entries starting with a suspicious digit.

## Warnings and data quality

- `data_quality.requirements`: at least 100 eligible amounts for a conclusion (`conformity` is `null` below).
- 422 when no line is on the selected accounts. Warnings come from `benford` (`SMALL_SAMPLE` under 300 amounts).

## Limitations

Some amounts never follow Benford's law: fixed prices, subscriptions, amounts with a cap. A deviation is a reason
to look, not a proof of fraud.
