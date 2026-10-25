# `utilization_forecast`

## Purpose

Forecasts the billable utilization of a consulting team over the coming months, the days on the bench, and who is
likely to be without a mission. Missions under way end or are renewed, deals in the pipeline are won or lost; the
use case estimates these probabilities from the history and simulates the team with
[`utilization_forecast`](../formulas/utilization_forecast.md).

**Status:** implemented in the Python package (`smerisk.usecases.utilization_forecast`; `smerisk.utilization_forecast`
is the formula) and served by the [HTTP service](../service.md).

## Python

Three fictitious consultants, two missions under way, one open deal:

```python
from smerisk.usecases import utilization_forecast


def mission(ref, consultant, customer, start, end, renewal_of=None):
    return {
        "mission_ref": ref,
        "consultant_ref": consultant,
        "customer_ref": customer,
        "start": start,
        "planned_end": end,
        "billing_type": "TIME",
        "renewal_of": renewal_of,
    }


consultants = [{"consultant_ref": k, "daily_cost": 420} for k in ("k1", "k2", "k3")]
missions = [
    mission("m1", "k1", "bank", "2025-01-06", "2025-06-30"),
    mission("m2", "k1", "bank", "2025-07-01", "2026-11-30", renewal_of="m1"),
    mission("m3", "k2", "insurer", "2025-09-01", "2026-12-31"),
]
deals = [
    {
        "deal_ref": "d1",
        "customer_ref": "retailer",
        "stage": "proposal",
        "amount": 90_000,
        "outcome": "OPEN",
        "expected_start": "2027-01-04",
        "consultants_needed": 1,
        "expected_duration_months": 4,
    }
]
out = utilization_forecast(
    consultants=consultants, missions=missions, deals=deals, settings={"as_of": "2026-10-20", "seed": 3}
)
print(
    [p.utilization_quantiles["0.5"] for p in out.result.by_period]
)  # [0.666667, 0.608696, 0.666667, 0.666667, 0.304348, 0.0]
print(round(out.derived_parameters["deals.d1.win_probability"].value, 3))  # 0.1
```

Without any closed deal, the win probability is the default for the only known stage (10%).

## HTTP

`POST /v1/usecases/utilization-forecast` takes the body below and answers `202` with a job to poll (see [jobs](../service.md#jobs-23)); the job's result is the response envelope.

```json
{"consultants": [{"consultant_ref": "k1", "daily_cost": 420}],
 "missions": [{"mission_ref": "m2", "consultant_ref": "k1", "customer_ref": "bank", "start": "2025-07-01",
               "planned_end": "2026-11-30", "billing_type": "TIME", "renewal_of": "m1"}],
 "timesheets": [], "deals": [],
 "settings": {"as_of": "2026-10-20", "horizon": 6}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `consultants` | records | yes | — | daily cost, available days by month (optional) |
| `missions` | records | yes | — | missions with `renewal_of` to count renewals |
| `timesheets` | records | no | — | billable days, planned leave |
| `deals` | records | no | — | pipeline, with stage history |
| `settings.horizon` | months | no | 6 | from the month after `as_of` |
| `settings.utilization_threshold` | rate | no | none | break-even utilization |
| `settings.renewal_prior` | `{mean, strength}` | no | 0.6, 5 | prior of the company-wide renewal rate |
| `settings.stages` | list | no | from histories | pipeline stages in order |
| `settings.n_simulations`, `seed`, `quantiles` | — | no | 10,000, drawn, standard | simulation |
| `settings.overrides` | object | no | — | paths of the table below |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `consultants.<ref>.available_days` | `available_days`, else working days minus planned leave |
| `consultants.<ref>.renewal_probability_beta` | Beta-Binomial posterior of the customer's renewals, shrunk toward the company |
| `consultants.<ref>.billable_days_per_period` | billable days on the mission over the last 6 months |
| `billable_days_per_period` | team mean, used for deals and missions without timesheets |
| `renewal_duration_months` | median length of past renewals; 3 months with fewer than 5 |
| `deals.<ref>.win_probability` | Beta-Binomial posterior mean of the deal's stage |

A mission is renewed when another mission names it in `renewal_of`, or when the consultant starts again at the same
customer within 5 working days. Open deals without an expected start or duration are left out.

## Output

The result of [`utilization_forecast`](../formulas/utilization_forecast.md): utilization and bench quantiles by
month, the probability for each consultant of being without a mission, and the billable days of won deals the team
could not staff.

## Warnings and data quality

- `data_quality.requirements`: 10 missions ended, and an expected start and duration for every open deal.
- 422 without consultants.

## Limitations

Any free consultant can staff any deal (no skills), and a renewal lasts a fixed number of months. The probability of
being on the bench is a planning aid, never a judgment of a person.
