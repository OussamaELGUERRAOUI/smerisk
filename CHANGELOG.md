# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/). Changing the random-number generator is a major-version change.

## [Unreleased]

## [0.1.0] - 2026-10-25

First release.

### Added

- Repository scaffold: `smerisk` library, `smerisk-service`, and continuous integration.
- `simulate_aggregate`: Monte Carlo aggregate simulation, rank correlations using a Gaussian copula, and variance contributions.
  Each ungrouped component and each correlation group has its own random stream.
- `risk_measures`: VaR, Expected Shortfall, and CFaR for a sample.
- `pert_estimate`: classical PERT three-point estimate, total effort at confidence levels, and contingency.
- `fixed_price_completion`: Monte Carlo final cost, margin, budget and deadline probabilities, and task and risk
  variance contributions, with one random stream per task and risk.
- `utilization_forecast`: Monte Carlo team utilization, bench days, per-consultant bench probability, and
  unstaffed deal days, with month-by-month staffing of won deals.
- `credibility`: Bühlmann-Straub credibility in `estimate` and `reference` modes.
- `beta_binomial`: conjugate Beta-Binomial rate with prior, equal-tailed credible interval, and prior weight.
- `kaplan_meier`: Kaplan-Meier survival with Greenwood variance, log-log confidence limits, small-group
  suppression, and the log-rank test.
- `markov_payments`: absorbing Markov chain of receivables with smoothed transitions, monthly projection, and
  default probabilities and payment delays by ageing state.
- `expected_loss`: expected credit loss by segment and concentration, with optional Monte Carlo loss quantiles
  and expected shortfall under independent defaults.
- `benford`: first-digit and first-two-digit Benford tests with Nigrini's MAD bands, z statistics, chi-square, and
  flagged labels.
- `newsvendor`: optimal perishable order quantity with expected sales, leftovers, shortage, and profit.
- `erlang_c`: M/M/c waiting probability, mean wait, service level, recommended team size, and staffing table.
- `brier`: Brier score, skill score against climatology or a given rate, and calibration bins.
- `kupiec`: proportion-of-failures likelihood-ratio test with verdict.
- Raw data contracts (`smerisk.contracts`): Pydantic models for payroll, accounting, consulting, retail, and
  forecast-log records; `parse_records` sets invalid records aside with their reason; `read_fec` reads FEC files
  (tab, `|`, or `;`, UTF-8 or Windows-1252, `Debit`/`Credit` or `Montant`/`Sens`) and drops labels.
- Parameter preparation (`smerisk.prepare`): monthly series to distributions (outliers, seasonality, trend,
  distribution choice), shrunk Spearman correlations, renewal, stage, and on-time payment rates, receivables
  transitions and invoices rebuilt from a lettered FEC, employment durations, arrivals and product demand, team
  daily cost, the data quality report, and traced overrides.
- Common foundation: response envelope, errors, warning codes, PCG64 seed management, nine probability distributions,
  and correlation-matrix adjustment.
- Business use cases (`smerisk.usecases`): `payroll_at_risk`, `cash_at_risk`, `rate_credibility`,
  `receivables_risk`, `attrition`, `ledger_audit`, `fixed_price_risk`, `utilization_forecast`, `fresh_orders`,
  `counter_staffing`, and `forecast_reliability`. Each takes raw records and settings, prepares the parameters, calls
  the formulas, and returns the envelope with `derived_parameters` and `data_quality`; overrides apply by path.
- HTTP service (`smerisk-service`, run from a clone of the repository; only the library goes to PyPI): the 15 formula routes and 11 use case routes under `/v1`, generated from the
  library's input models; bearer tokens, one per calling application; jobs for simulations (`GET /v1/jobs/{id}`,
  results kept 24 hours); reusable datasets encrypted in memory (`/v1/datasets`, FEC upload in multipart); errors
  422, 409, 413, 429, and 500 with a trace identifier; request size limit; OpenAPI contract written by the CI. Datasets can be sent in numbered pages
  (`PUT /v1/datasets/{id}/pages/{n}`, safe to retry, any order) with the number of pages given at creation or with
  `complete`; a use case answers 409 `DATASET_INCOMPLETE` while a page is missing. Requests can be compressed with gzip, with a limit on
  the decompressed size. Structured logs (JSON or text): one event per request, job, and dataset operation, with an
  `X-Request-ID` that is also the trace id of a 500; no body, record, token, or exception message is ever logged.
- `simulate_aggregate`: optional `persistent` components and groups (one draw for the whole horizon) and
  `orientation` (`lower_is_better` measures the risk of a cost overrun). Results of existing inputs are unchanged.

### Changed

- Translated package source comments, docstrings, validation messages, and project documentation into English.
- Added English formula documentation for all contracts in the API specification.
