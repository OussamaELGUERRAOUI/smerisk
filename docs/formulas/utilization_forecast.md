# `utilization_forecast`

## Purpose

Simulates a consulting team's billable utilization and bench days month by month, accounting for current assignments,
renewal uncertainty, and open deals. A small consultancy can use the forecast for capacity and hiring decisions.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

```python
from smerisk import utilization_forecast

months = ["2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06"]
days = {month: 20 for month in months}
result = utilization_forecast(
    periods=months,
    consultants=[
        {
            "id": "consultant-1",
            "available_days": days,
            "current_mission": {
                "end_period": "2027-02",
                "billable_days_per_period": 18,
                "renewal_probability": 0.6,
                "renewal_duration_months": {"type": "fixed", "value": 3},
            },
        },
        {
            "id": "consultant-2",
            "available_days": days,
            "current_mission": {"end_period": "2027-06", "billable_days_per_period": 20, "renewal_probability": 0},
        },
        {"id": "consultant-3", "available_days": days},
    ],
    deals=[
        {
            "id": "deal-1",
            "win_probability": 0.5,
            "start_period": "2027-03",
            "consultants_needed": 2,
            "billable_days_per_period": 19,
            "duration_months": 4,
        }
    ],
    utilization_threshold=0.7,
    seed=42,
)
march = result.result.by_period[2]
print(march.utilization_quantiles["0.5"])  # 0.95
print(march.p_utilization_below)  # 0.4958: deal lost
print(result.result.consultants[0].p_on_bench["2027-03"])  # 0.1927: not renewed and deal lost (0.4 × 0.5)
print(result.result.unstaffed_deal_days.expected)  # 17.33: about 0.3 × 57 days
print(result.result.unstaffed_deal_days.quantiles["0.95"])  # 57.0: one seat open March to May
```

`consultant-1` ends its mission in February and renews for three months with probability 0.6. If the deal is won
while `consultant-1` is renewed, only `consultant-3` is free in March: one seat stays open until `consultant-1` frees
up in June. Each consultant and each deal draws from its own random stream, so adding a deal and rerunning with the
same seed leaves the other draws unchanged.

## HTTP

`POST /v1/utilization/forecast` takes the body below and answers `202` with a job to poll (see [jobs](../service.md#jobs-23)); the job's result is the response envelope.

```json
{
  "periods": ["2026-01"],
  "consultants": [
    {"id": "consultant-1", "available_days": {"2026-01": 20}, "current_mission": null}
  ],
  "deals": [],
  "n_simulations": 10000,
  "seed": 42
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope. `assumptions` records the
staffing rule, the month rounding, and the random-stream layout.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `periods` | consecutive months, in order | Yes | Forecast horizon, at most 60 |
| `consultants[].id` | string | Yes | Opaque consultant identifier |
| `consultants[].available_days` | month-to-number object | Yes | Available workdays after planned leave, training, and absence; a missing month counts as 0 |
| `consultants[].current_mission` | object or null | No | `end_period` (last month), `billable_days_per_period`, exactly one renewal input, and `renewal_duration_months` (distribution; required unless `renewal_probability` is 0) |
| `current_mission.renewal_probability` | rate | One of the renewal inputs | Probability from 0 to 1 |
| `current_mission.renewal_probability_beta` | `{alpha, beta}` | One of the renewal inputs | Beta prior/posterior parameters (§5.2) |
| `deals[].id` | string | Yes, when deals supplied | Opaque deal identifier |
| `deals[].win_probability` | rate | Yes, when deals supplied | Probability from 0 to 1 |
| `deals[].start_period` | month or distribution over month index | Yes, when deals supplied | Start if won; index 0 is the first period; drawn values are rounded |
| `deals[].consultants_needed` | integer ≥ 1 | Yes, when deals supplied | Required consultants |
| `deals[].billable_days_per_period` | number | Yes, when deals supplied | Billable days |
| `deals[].duration_months` | number or distribution | Yes, when deals supplied | Duration, rounded to whole months, minimum 1 |
| `utilization_threshold` | rate | No | Optional threshold for below-target probability |
| `n_simulations` | integer | No | As §3.1; default 10,000 |
| `seed` | non-negative integer | No | Generated and returned if omitted |
| `quantiles` | list of levels | No | Default `[0.05, 0.10, 0.50, 0.90, 0.95]`, as §3.1 |

## Calculation

Each simulation draws, independently, whether each current mission renews (with a probability drawn from its Beta
distribution when `renewal_probability_beta` is given) and for how many months, and whether each deal is won, with
its start month and duration. Then, month by month, the open seats of won deals that are running are filled with
free consultants:

- deals in order of start month, then input order;
- consultants in order of availability: free for the longest time first, then input order.

An assigned consultant stays on the deal until it ends. A seat that cannot be filled stays open and can be filled in
a later month; until then its billable days count in `unstaffed_deal_days`. As specified, any free consultant can
staff any deal (no skills). Billable days are capped by each consultant's available days.

```text
utilization[t] = total_billable_days[t] / total_available_days[t]
bench_days[t]  = available days of consultants with no mission or deal in month t
```

Reference: Vose, *Risk Analysis: A Quantitative Guide*, 3rd ed., Wiley, 2008 (Monte Carlo risk analysis).

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `by_period[].utilization_quantiles` | Quantiles of team utilization by month |
| `by_period[].bench_days_quantiles` | Quantiles of total bench days by month |
| `by_period[].p_utilization_below` | Probability utilization is below the optional threshold; `null` without one |
| `consultants[].p_on_bench` | Per-consultant and per-month bench probability; an operational planning metric, not an individual performance measure |
| `unstaffed_deal_days` | `expected` and `quantiles` of won-deal billable days over the horizon that the team cannot staff |

## Warnings and errors

- `HIGH_UNCERTAINTY`: the 90% interval of horizon utilization is wider than its median.
- `422` (`InvalidInput`): non-consecutive or weekly periods, duplicate consultant or deal ids, an `available_days`
  month outside `periods`, a month where no consultant is available, both or neither renewal inputs, or a missing
  renewal duration.
- `413` (`InputTooLarge`): more than 60 periods, 100,000 simulations, or 200 consultants and deals combined.

## Limitations

The assignment model does not consider skills, preferences, labor rules, or optimization of staffing. It assumes the
provided renewal, win, duration, availability, and billable-day inputs describe the future. No utilization value
should be interpreted as a measure of an individual consultant's performance.
