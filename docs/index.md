---
title: smerisk, probabilistic management control in Python
description: Open-source Python library for payroll at risk, cash-flow at risk (CFaR), credibility-weighted rates, fixed-price project risk, receivables risk and Benford audits.
---

# smerisk

smerisk is an open-source Python library for probabilistic management control in SMEs: payroll at risk, cash-flow at
risk (CFaR), credibility-weighted rates, fixed-price project risk, receivables risk and Benford audits.

Each function takes data a business application already stores (or plain parameters), runs a probabilistic or
statistical calculation, and returns the result with the assumptions used (including the random seed), warnings and
data-quality information. The same functions are served over HTTP by an optional FastAPI service.

## Install

```bash
pip install smerisk
```

## Example

Value at Risk and Expected Shortfall of a sample of outcomes:

```python
from smerisk import risk_measures

result = risk_measures(
    samples=[float(value) for value in range(1, 101)],
    confidence=0.95,
)

print(round(result.result.quantile, 2))  # 5.95
print(round(result.result.var, 2))  # 44.55
print(result.result.expected_shortfall)  # 3.0
```

A complete example with raw payslips is on [Payroll at risk in Python](usecases/payroll_at_risk.md).

## Use cases

- [Payroll at risk](usecases/payroll_at_risk.md): payroll cost of the coming months, variable parts and hires.
- [Cash-flow at risk (CFaR)](usecases/cash_at_risk.md): weekly bank balance and probability of running short.
- [Credible absenteeism and turnover rates](usecases/rate_credibility.md): Bühlmann-Straub credibility per group.
- [Receivables risk](usecases/receivables_risk.md): ageing of receivables and expected loss.
- [Employee attrition](usecases/attrition.md): retention curves with Kaplan-Meier.
- [Benford audit of FEC entries](usecases/ledger_audit.md): screening of ledger amounts.
- [Fixed-price project risk](usecases/fixed_price_risk.md): final cost, margin and deadline by Monte Carlo.
- [Consulting team utilization](usecases/utilization_forecast.md), [perishable goods orders](usecases/fresh_orders.md),
  [counter staffing with Erlang C](usecases/counter_staffing.md) and
  [forecast reliability](usecases/forecast_reliability.md).

## Formulas

Fifteen building blocks, one page each: [Monte Carlo aggregate simulation](formulas/simulate_aggregate.md),
[VaR, Expected Shortfall and CFaR](formulas/risk_measures.md), [PERT](formulas/pert_estimate.md),
[fixed-price completion](formulas/fixed_price_completion.md),
[utilization](formulas/utilization_forecast.md), [Bühlmann-Straub credibility](formulas/credibility.md),
[Beta-Binomial](formulas/beta_binomial.md), [Kaplan-Meier](formulas/kaplan_meier.md),
[Markov chain of receivables](formulas/markov_payments.md), [expected loss](formulas/expected_loss.md),
[Benford's law](formulas/benford.md), [newsvendor](formulas/newsvendor.md), [Erlang C](formulas/erlang_c.md),
[Brier score](formulas/brier.md) and [Kupiec test](formulas/kupiec.md).

## Reference

- [Reference index](reference.md): every function with its HTTP endpoint.
- [Raw data contracts](data-contracts.md) and [parameter preparation](parameter-preparation.md).
- [HTTP service](service.md): run the FastAPI service and call the functions from any language.
- [`llms.txt`](llms.txt): a plain-text map of this site for AI assistants.

smerisk is released under the Apache License 2.0. Source code and issues:
[GitHub](https://github.com/OussamaELGUERRAOUI/smerisk). The library is in alpha: the API may change before 1.0.
