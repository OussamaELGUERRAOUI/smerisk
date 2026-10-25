---
title: Parameter preparation from raw SME data
description: How smerisk estimates model parameters from raw records, reports data quality, and accepts overrides.
---

# Parameter preparation from raw SME data

**Status:** implemented (`smerisk.prepare`). The [use cases](reference.md#use-cases) that chain preparation and formulas are implemented.

## Purpose

The use cases receive [raw data](data-contracts.md) and must pass parameters to the formulas: the distribution of
overtime, the renewal rate of a customer, a transition matrix. `smerisk.prepare` holds the estimators that turn one
into the other (specification §11). They are deterministic: the same data, settings, and library version always give
the same parameters.

Each estimator returns an `Estimate`: the `value`, its `source` (`estimated`, `default`, or `override`), the
`method` in words, the number of points used (`n_points`), and the period covered (`first`, `last`).

## Monthly series → distribution

`fit_series` fits the distribution of one component, such as the overtime cost, from its monthly amounts:

1. **Months**: `monthly_totals` sums the records by month; a month without data is missing, never 0.
2. **Outliers**: a month more than 5 median absolute deviations from the median is flagged. It stays in the data
   unless `exclude_outliers=True`. When most months are equal (MAD = 0), the mean absolute deviation is used instead.
3. **Seasonality**: with at least 24 months, the index of a calendar month is the mean of that month divided by the
   overall mean. The series is deseasonalized, and the index of the forecast month is applied again. With fewer
   months: warning `NO_SEASONALITY`. A calendar month that is always 0, such as a bonus paid only in December, is
   forecast at 0.
4. **Distribution**: `fixed` at the mean under 6 months (`SMALL_SAMPLE`); `lognormal` from the observed median and
   90th percentile if the values are never negative and their skewness is above 1; otherwise `normal`, truncated at
   0 when the quantity cannot be negative. `method="bootstrap"` gives an `empirical` distribution from 20 months.
5. **Trend**: not extrapolated by default. `trend="linear"` applies a trend when there are at least 24 months and the
   slope is significant (p < 0.05). The seasonal index is then measured against the trend line, not the mean.

```python
from smerisk.prepare import fit_series, monthly_totals

overtime = monthly_totals(
    [
        ("2025-09", 1650),
        ("2025-10", 1720),
        ("2025-11", 1810),
        ("2025-12", 2950),
        ("2026-01", 1580),
        ("2026-02", 1690),
        ("2026-03", 1760),
        ("2026-04", 1840),
        ("2026-05", 1700),
        ("2026-06", 1930),
        ("2026-07", 2100),
        ("2026-08", 1620),
    ]
)
fit = fit_series(overtime, name="overtime")
estimate = fit.estimate("2027-01")
print(estimate.value)  # type='lognormal' median=1740.0 p90=2083.0
print([w.code.value for w in fit.warnings])  # ['NO_SEASONALITY']: 12 months, 24 needed
print([(o.period, o.value) for o in fit.outliers])  # [('2025-12', 2950.0)]: flagged, still used
```

## Correlations

`shrunk_spearman` measures the Spearman correlation of two monthly series on their common months and shrinks it
toward 0: `rho × n / (n + 10)`. With fewer than 12 common months, or a constant series, the use case's default is
used with the warning `DEFAULT_CORRELATION`. `estimate_correlations` does this for every pair, and
`correlation_matrix` builds the matrix once overrides are applied. The formula makes it positive definite if needed.

## Rates, transitions, and durations

| Function | Estimates | From |
| --- | --- | --- |
| `renewal_rates` | renewal probability, company-wide and per customer | `missions` |
| `stage_rates` | win probability per pipeline stage | closed `deals` |
| `on_time_rates` | probability of payment at most 5 days after the due date, per customer | `invoices` |
| `transitions` | month-end to month-end moves between ageing states, weighted by amount due | `invoices` |
| `late_payment_delay` | days late of invoices paid late, as a lognormal distribution | `invoices` |
| `survival_subjects` | durations of employment, exits, and censoring | `employment_periods` |
| `arrival_rates` | mean tickets per site, weekday, and hour, and whether arrivals are irregular | `sales` |
| `daily_demand`, `demand_distribution` | daily demand of a product | `sales` |
| `team_daily_cost`, `actual_cost` | daily cost of a project team, and cost already spent | `consultants`, `timesheets` |

Rates are returned as counts with a prior, ready for `beta_binomial`:

- Each customer's rate is shrunk toward the company-wide rate, which weighs as 5 observations.
- The company-wide renewal rate starts from a prior of 60% with strength 5.
- Stage priors are 10%, 25%, 50%, and 75% by rank, with strength 4.

A mission is renewed when another mission names it in `renewal_of`, or when the same consultant starts again at the
same customer within 5 business days. An outcome that is not known yet is left out: a mission that ended less than
5 business days ago, or an invoice due less than 5 days ago and still unpaid.

`invoices_from_ledger` rebuilds invoices from a lettered FEC. The FEC has no due date, so the due date is the issue
date plus 30 days by default.

`merge_contracts` merges successive contracts at most one day apart into one period of employment. An exit for the
end of a fixed-term contract or a retirement is a censoring, not an event, unless told otherwise.

Demand is measured from sales. A day that sold out understates the demand of that day.

## Data quality

`QualityReport` collects, while the data is prepared:

- the coverage of each contract and series;
- the missing months;
- the outliers;
- the ignored records (at most 100 listed);
- the minimums required.

`build()` returns the `data_quality` of the response. It is `usable` only when every minimum is met.

## Derived parameters and overrides

`DerivedParameters` holds every parameter passed to the formulas, by path, with its source. The caller can impose
any of them with `overrides`. A correlation can be named in either order (`overtime~sick_pay` or
`sick_pay~overtime`). An unknown path or an invalid value raises 422, and no override is applied.

```python
from smerisk.core.distributions import Lognormal
from smerisk.prepare import DerivedParameters, Estimate

params = DerivedParameters()
overtime = Estimate(value=Lognormal(median=1740, p90=2083), source="estimated", method="lognormal", n_points=12)
params.add("components.overtime", overtime)
params.default("components.planned_hires.probability", 0.8)
params.apply({"components.planned_hires.probability": 0.9})
print(
    {path: e.source for path, e in params.to_dict().items()}
)  # {'components.overtime': 'estimated', 'components.planned_hires.probability': 'override'}
```
