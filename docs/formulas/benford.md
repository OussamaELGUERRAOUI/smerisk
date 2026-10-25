# `benford`

## Purpose

Compares leading-digit frequencies in a set of amounts with Benford's Law. It can help an auditor prioritize
accounting entries for review, but a deviation is a screening signal—not evidence of fraud.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Purchase entries in which 60 payments of 480 € were split to stay below a 500 € approval threshold (fictitious data):

```python
import numpy as np

from smerisk import benford

rng = np.random.default_rng(7)
amounts = [round(float(a), 2) for a in 10 ** rng.uniform(1, 5, 1_200)] + [480.0] * 60
result = benford(amounts=amounts, labels=[f"entry-{i}" for i in range(len(amounts))])
print(result.result.n_used)  # 1260
print(round(result.result.mad, 4))  # 0.0164
print(result.result.conformity)  # NONCONFORMITY
print(result.result.suspicious_digits)  # [4]: excess of amounts starting with 4
print(len(result.result.flagged_labels))  # 174: entries to examine, not proof of fraud
```

Leading digits are read from absolute amounts rounded to the cent, so float noise such as `299.99999999999997`
counts as 300.00.

## HTTP

`POST /v1/audit/benford` takes the body below and returns the response envelope.

```json
{
  "amounts": [1250.40, 89.90, 4800.00],
  "min_amount": 10,
  "test": "first_digit"
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `amounts` | list of numbers | Yes | Currency values; absolute values are analyzed |
| `min_amount` | number ≥ 0 | No | Exclude smaller absolute amounts; default `10` |
| `test` | `first_digit` or `first_two_digits` | No | Default `first_digit` |
| `labels` | list of strings | No | Opaque identifiers, same length as `amounts`; returned for flagged values |

## Calculation

For leading digit `d`, the expected proportion is:

```text
P(d) = log10(1 + 1/d)
MAD = (1/K) * sum(abs(observed[d] - P(d)))
```

`K` is 9 for the first-digit test and 90 for the first-two-digit test. MAD is the primary conformity measure; a
chi-square statistic is included as secondary information. The specified first-digit MAD bands are `<0.006`
(close conformity), `0.006–0.012` (acceptable), `0.012–0.015` (marginal), and `>0.015` (nonconformity). For
the first-two-digit test, the corresponding cutoffs are `0.0012`, `0.0018`, and `0.0022`.

Reference: Nigrini, *Benford's Law: Applications for Forensic Accounting, Auditing, and Fraud Detection*, Wiley,
2012.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `n_used`, `n_excluded` | Number of included and excluded amounts |
| `digits[]` | `digit`, `observed` and `expected` proportions, and Nigrini's `z` (absolute, with continuity correction) |
| `mad`, `conformity` | Mean absolute deviation and its category: `CLOSE`, `ACCEPTABLE`, `MARGINAL`, or `NONCONFORMITY`; `conformity` is `null` below 100 amounts |
| `chi2`, `p_value` | Chi-square statistic and p-value (8 or 89 degrees of freedom) |
| `suspicious_digits` | Digits observed more often than expected with `z > 1.96` |
| `flagged_labels` | Supplied labels associated with suspicious leading digits |

## Warnings and errors

- `SMALL_SAMPLE`: fewer than 300 eligible amounts; below 100, `conformity` is `null`.
- `422` (`InvalidInput`): `labels` of a different length than `amounts`, an empty list, an unknown `test`, or a
  negative `min_amount`.
- `409` (`ComputationImpossible`, `NO_ELIGIBLE_AMOUNTS`): no amount reaches `min_amount`.
- `413` (`InputTooLarge`): more than 50,000 amounts.

## Limitations

Benford's Law is appropriate only for naturally generated values spanning multiple scales. It is generally unsuitable
for assigned identifiers, constrained ranges, prices set by policy, or small homogeneous samples. Conformity does not
prove data are valid, and nonconformity does not prove manipulation.
