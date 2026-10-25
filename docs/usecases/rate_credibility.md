# `rate_credibility`

## Purpose

Gives each site or team a credible absenteeism or turnover rate. A small team's own rate swings with a single long
absence; the use case weighs it against a reference with Bühlmann-Straub credibility, so that small groups are not
judged on noise. It builds monthly rates from raw data and calls [`credibility`](../formulas/credibility.md).

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

Three fictitious sites over 21 months, with the sector rate of 5% as reference:

```python
from smerisk.usecases import rate_credibility

rows = []
for site, headcount, rate in [("lyon", 40, 0.045), ("lille", 12, 0.08), ("nantes", 6, 0.03)]:
    for month in [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]:
        theoretical = 21.0 * headcount
        rows.append(
            {
                "period": month,
                "group": site,
                "absence_days": round(theoretical * rate, 1),
                "theoretical_days": theoretical,
                "headcount": headcount,
            }
        )
out = rate_credibility(absence_months=rows, settings={"as_of": "2026-10-20", "reference_mean": 0.05})
for group in out.result.groups:
    print(group.id, round(group.observed_mean, 4), round(group.credibility, 2), round(group.estimate, 4))
```

Output:

```text
lille 0.0802 0.57 0.0672
lyon 0.045 0.82 0.0459
nantes 0.0302 0.4 0.0421
```

Nantes is small: its low observed rate counts for 40% only.

## HTTP

`POST /v1/usecases/rate-credibility` takes the body below and returns the response envelope.

```json
{"absence_months": [{"period": "2026-01", "group": "lyon", "absence_days": 37.8, "theoretical_days": 840,
                     "headcount": 40}],
 "settings": {"as_of": "2026-10-20", "indicator": "absenteeism", "reference_mean": 0.05}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `absence_months` | records | for absenteeism | — | aggregated by month and group, never by employee |
| `employment_periods` | records | for turnover | — | periods (or contracts) of employment |
| `settings.indicator` | `absenteeism`, `turnover` | no | `absenteeism` | rate measured |
| `settings.history_months` | months | no | 24 | complete months before the month of `as_of` |
| `settings.group_by` | `qualification`, `site_ref`, `contract_type` | no | none | turnover only: groups |
| `settings.reference_mean` | rate | with one group | rate of all groups | reference rate |
| `settings.credibility_constant` | exposure | no | estimated, or publisher value | k |
| `settings.overrides` | object | no | — | `groups`, `reference_mean`, `credibility_constant` |

## Preparation

| Rate | Monthly observation | Weight |
| --- | --- | --- |
| absenteeism | absence days / theoretical days | theoretical days |
| turnover | exits / employee-months present | employee-months |

Groups of fewer than 3 employees on average are merged into `others`. With 5 groups or more and no constant, the
constant is estimated (`mode: estimate`). Otherwise the reference mode is used, with `reference_mean` (or the rate
of all groups together) and the constant given, or the publisher value: 4,000 theoretical days for absenteeism,
600 employee-months for turnover. A group with that exposure gets a credibility of 0.5.

## Output

The result of [`credibility`](../formulas/credibility.md): for each group its exposure, observed rate, credibility,
and credible estimate, with the parameters used.

## Warnings and data quality

- `data_quality.requirements`: 12 months of data.
- 422 without data in the period, or with a single group and no `reference_mean`. Warnings come from
  `credibility` (`NO_BETWEEN_GROUP_VARIANCE`, `SMALL_SAMPLE`).

## Limitations

The publisher constants are orders of magnitude for small French employers (daily absence rate near 5%, monthly exit
rate near 1.5%); they should be replaced by an estimate as soon as 5 groups or more are available.
