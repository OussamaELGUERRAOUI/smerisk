# `counter_staffing`

## Purpose

Tells how many people to put at the counter for each site, weekday, and hour so that most customers wait less than a
target time. Arrivals are the receipts issued in the same slot over the last 8 weeks; the team size comes from
[`erlang_c`](../formulas/erlang_c.md).

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

A fictitious takeaway on Saturdays, 3 minutes per customer, 80% served within 3 minutes:

```python
import datetime as dt

from smerisk.usecases import counter_staffing

saturdays = [dt.date(2026, 10, 17) - dt.timedelta(weeks=w) for w in range(8)]
sales = [
    {"site_ref": "takeaway", "date": d.isoformat(), "hour": hour, "tickets": n, "quantity": n, "revenue_excl_tax": 0}
    for d in saturdays
    for hour, n in [(12, 62), (13, 48), (19, 35)]
]
out = counter_staffing(sales=sales, settings={"as_of": "2026-10-20", "service_time_minutes": 3})
print([(s.hour, s.staffing.recommended_agents) for s in out.result.slots])  # [(12, 5), (13, 4), (19, 3)]
```

## HTTP

`POST /v1/usecases/counter-staffing` takes the body below and returns the response envelope.

```json
{"sales": [{"site_ref": "takeaway", "date": "2026-10-17", "hour": 12, "tickets": 62, "quantity": 62,
            "revenue_excl_tax": 0}],
 "settings": {"as_of": "2026-10-20", "service_time_minutes": 3}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `sales` | records | yes | — | sales with `hour` and `tickets` |
| `settings.service_time_minutes` | minutes | yes | — | mean time to serve one customer |
| `settings.target_wait_seconds` | seconds | no | 180 | acceptable wait |
| `settings.target_service_level` | rate | no | 0.8 | share of customers served within the wait |
| `settings.site_ref` | string | no | all sites | restrict to one site |
| `settings.as_of` | date | no | today | later sales are ignored |
| `settings.overrides` | object | no | — | `arrival_rate.<site>.<weekday>.<hour>` |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `arrival_rate.<site>.<weekday>.<hour>` | mean receipts in that hour over the days of that weekday with receipts, 8 weeks |

An hour without a record on a day the site was open counts as 0 receipts.

## Output

`slots[]`: `site_ref`, `weekday` (0 = Monday), `hour`, `arrival_rate`, `irregular` (variance above twice the mean:
busier and quieter days than a Poisson flow), and the [`erlang_c`](../formulas/erlang_c.md) `staffing`, or `null`
when no customer is expected.

## Warnings and data quality

- `data_quality.requirements`: 4 days per slot.
- `data_quality.outliers`: slots with irregular arrivals.
- 422 without a sale with hour and tickets in the 8 weeks.

## Limitations

The Erlang C model assumes random arrivals at a steady rate within the hour and customers who wait. A rush at the
start of the hour, or customers who leave when the queue is long, need a margin above the recommendation, as do
slots flagged irregular.
