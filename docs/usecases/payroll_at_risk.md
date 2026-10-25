# `payroll_at_risk`

## Purpose

Forecasts the payroll cost of the coming months with its uncertainty. The payroll engine already knows the fixed
part; overtime, night work, bonuses, sick pay, and planned hires vary. The use case fits each variable part on the
payslip history (seasonality included), correlates them, and simulates the total with
[`simulate_aggregate`](../formulas/simulate_aggregate.md). The risk is an overrun: the payroll at risk is the cost
above the mean at the confidence level.

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

Ten fictitious employees over 24 months, and the baseline of the next three months with one hire in December:

```python
from smerisk.usecases import payroll_at_risk

months = [f"2024-{m:02d}" for m in range(10, 13)] + [f"2025-{m:02d}" for m in range(1, 13)]
months += [f"2026-{m:02d}" for m in range(1, 10)]
payslips = [
    {
        "employee_ref": f"e{e}",
        "period": month,
        "gross": 2_600,
        "employer_contributions": 730,
        "employer_cost": 3_330,
        "by_family": {"base": 2_300, "overtime": 120 + 40 * ((i + e) % 5), "sick_pay": 60 * ((i * e) % 3)},
    }
    for i, month in enumerate(months)
    for e in range(10)
]
baseline = [
    {"period": "2026-11", "fixed_cost": 30_000},
    {"period": "2026-12", "fixed_cost": 30_000, "planned_hires": [{"start_period": "2026-12", "monthly_cost": 3_200}]},
    {"period": "2027-01", "fixed_cost": 30_600},
]
out = payroll_at_risk(payslips=payslips, payroll_baseline=baseline, settings={"as_of": "2026-10-20", "seed": 7})
print([c.id for c in out.result.contributions])  # ['fixed_cost', 'overtime', 'sick_pay', 'hire.2026-12.1']
print([p.expected_flow for p in out.result.by_period])  # [32540.0, 35080.8, 35140.8]
print(out.result.horizon.cfar)  # 1318.4
```

Over the three months, the payroll exceeds its mean by more than 1,318 € with a 5% probability.

## HTTP

`POST /v1/usecases/payroll-at-risk` takes the body below and answers `202` with a job to poll (see [jobs](../service.md#jobs-23)); the job's result is the response envelope.

```json
{"payslips": [{"employee_ref": "e1", "period": "2026-01", "gross": 2150.00, "employer_contributions": 610.40,
               "employer_cost": 2760.40, "by_family": {"base": 1900.00, "overtime": 180.00, "night": 70.00}}],
 "payroll_baseline": [{"period": "2027-01", "fixed_cost": 48200.00,
                       "planned_hires": [{"start_period": "2027-03", "monthly_cost": 2900.00}]}],
 "settings": {"seed": 7, "quantiles": [0.1, 0.5, 0.9]}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `payslips` | records | yes | — | validated payslips with `by_family` |
| `payroll_baseline` | records | yes | — | months to forecast: fixed cost and planned hires |
| `settings.history_months` | months | no | 24 | complete months of payslips before the month of `as_of` |
| `settings.n_simulations`, `seed`, `confidence`, `quantiles` | — | no | 10,000, drawn, 0.95, standard | simulation |
| `settings.overrides` | object | no | — | paths of the table below |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `components.<family>` | monthly totals of `overtime`, `night`, `bonus`, `sick_pay`, fitted as in [§11.1](../parameter-preparation.md): one distribution per forecast month |
| `correlation.<a>~<b>` | shrunk Spearman correlation of two families; default 0.3 for `overtime~sick_pay`, 0 otherwise |
| `components.planned_hires.probability` | 0.8 for a hire without a probability; an override applies to every hire |

A family absent from the payslips is 0 (override it to add a risk). A hire is drawn once: it happens for all its
months or for none.

## Output

The result of [`simulate_aggregate`](../formulas/simulate_aggregate.md) with `orientation: lower_is_better`: payroll
quantiles by month and cumulated, `horizon.cfar` (cost above the mean at the confidence level),
`horizon.expected_shortfall` (mean cost in the worst cases), and the contribution of each part to the variance.

## Warnings and data quality

- `data_quality.requirements`: 12 months of payslips, with `by_family`.
- Warnings from the preparation: `NO_SEASONALITY` under 24 months, `SMALL_SAMPLE` under 6, `DEFAULT_CORRELATION`.

## Limitations

Each family is assumed independent from one month to the next; a lasting rise of overtime is only captured through
the trend option of the preparation. The headcount is the one of the baseline: departures not planned are not
simulated.
