# smerisk documentation

There is one page per formula and per use case. Each page describes its purpose, input and output contracts,
calculation, warnings, errors, and limitations. The contracts are based on the specification (§3–§12). The specification is currently
kept outside this repository and remains the source of truth.

**Implementation status:** all fifteen formulas and eleven use cases are implemented in the Python package and served
by the HTTP service under the endpoints below; see [HTTP service](service.md).

## Formula reference

| Domain | Function | Endpoint | Documentation | Status |
| --- | --- | --- | --- | --- |
| Simulation | `simulate_aggregate` | `POST /v1/simulation/aggregate` | [simulate_aggregate](formulas/simulate_aggregate.md) | Implemented |
| Simulation | `risk_measures` | `POST /v1/risk/measures` | [risk_measures](formulas/risk_measures.md) | Implemented |
| Projects | `pert_estimate` | `POST /v1/pert/estimate` | [pert_estimate](formulas/pert_estimate.md) | Implemented |
| Projects | `fixed_price_completion` | `POST /v1/projects/fixed-price-completion` | [fixed_price_completion](formulas/fixed_price_completion.md) | Implemented |
| Projects | `utilization_forecast` | `POST /v1/utilization/forecast` | [utilization_forecast](formulas/utilization_forecast.md) | Implemented |
| Rates | `credibility` | `POST /v1/bayes/credibility` | [credibility](formulas/credibility.md) | Implemented |
| Rates | `beta_binomial` | `POST /v1/bayes/beta-binomial` | [beta_binomial](formulas/beta_binomial.md) | Implemented |
| Durations | `kaplan_meier` | `POST /v1/survival/kaplan-meier` | [kaplan_meier](formulas/kaplan_meier.md) | Implemented |
| Receivables | `markov_payments` | `POST /v1/credit/markov-payments` | [markov_payments](formulas/markov_payments.md) | Implemented |
| Receivables | `expected_loss` | `POST /v1/credit/expected-loss` | [expected_loss](formulas/expected_loss.md) | Implemented |
| Audit | `benford` | `POST /v1/audit/benford` | [benford](formulas/benford.md) | Implemented |
| Operations | `newsvendor` | `POST /v1/operations/newsvendor` | [newsvendor](formulas/newsvendor.md) | Implemented |
| Operations | `erlang_c` | `POST /v1/operations/erlang-c` | [erlang_c](formulas/erlang_c.md) | Implemented |
| Forecast reliability | `brier` | `POST /v1/validation/brier` | [brier](formulas/brier.md) | Implemented |
| Forecast reliability | `kupiec` | `POST /v1/validation/kupiec` | [kupiec](formulas/kupiec.md) | Implemented |

## Use cases

The use cases (specification §12) take raw data, prepare the parameters, call the formulas, and return the usual
envelope with `derived_parameters` (each parameter used, with its source) and `data_quality`. They are implemented in
`smerisk.usecases` and served under `/v1/usecases/…`.

| Function | Endpoint | Answers | Documentation |
| --- | --- | --- | --- |
| `payroll_at_risk` | `POST /v1/usecases/payroll-at-risk` | payroll cost of the coming months, variable parts and hires | [payroll_at_risk](usecases/payroll_at_risk.md) |
| `cash_at_risk` | `POST /v1/usecases/cash-at-risk` | weekly bank balance and probability of running short | [cash_at_risk](usecases/cash_at_risk.md) |
| `rate_credibility` | `POST /v1/usecases/rate-credibility` | credible absenteeism or turnover rate per group | [rate_credibility](usecases/rate_credibility.md) |
| `receivables_risk` | `POST /v1/usecases/receivables-risk` | ageing of receivables and expected loss | [receivables_risk](usecases/receivables_risk.md) |
| `attrition` | `POST /v1/usecases/attrition` | retention curves of employees | [attrition](usecases/attrition.md) |
| `ledger_audit` | `POST /v1/usecases/ledger-audit` | Benford's test on ledger amounts | [ledger_audit](usecases/ledger_audit.md) |
| `fixed_price_risk` | `POST /v1/usecases/fixed-price-risk` | final cost, margin, and deadline of fixed-price projects | [fixed_price_risk](usecases/fixed_price_risk.md) |
| `utilization_forecast` | `POST /v1/usecases/utilization-forecast` | billable utilization and bench of a consulting team | [utilization_forecast](usecases/utilization_forecast.md) |
| `fresh_orders` | `POST /v1/usecases/fresh-orders` | quantities of perishable products to prepare | [fresh_orders](usecases/fresh_orders.md) |
| `counter_staffing` | `POST /v1/usecases/counter-staffing` | agents needed at the counter by hour | [counter_staffing](usecases/counter_staffing.md) |
| `forecast_reliability` | `POST /v1/usecases/forecast-reliability` | reliability of past probabilities and intervals | [forecast_reliability](usecases/forecast_reliability.md) |

## Raw data and service

The use cases (specification §12) take the records a business application already stores rather than parameters.
[Raw data contracts](data-contracts.md) describes them, the handling of invalid records, and how a FEC file is read.
[Parameter preparation](parameter-preparation.md) describes how the parameters of the formulas are estimated from
them, the data quality report, and overrides. [HTTP service](service.md) describes how to run the service, its
tokens, jobs, datasets, and errors.

## Shared conventions

The specified response envelope contains `result`, `assumptions`, `warnings`, and `meta`. Randomized calculations
record their seed in `assumptions`. Shared error and warning contracts are described in the external API
specification (§2.1–§2.6).
