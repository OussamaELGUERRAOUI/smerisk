---
title: Newsvendor order quantity in Python
description: Find the quantity to order or produce for one selling period from a demand sample and overage and shortage costs.
---

# Newsvendor order quantity in Python

Function: `newsvendor`

## Purpose

Finds the order or production quantity that balances overage and shortage costs for a single selling period. A
restaurant or small retailer can use it to plan perishable products such as prepared food or fresh inventory.

**Status:** implemented in the Python package and served by the [HTTP service](../service.md).

## Python

Daily quiches of a bakery: sales of the last 20 same weekdays, sold 6.50 €, cost 2.20 €, 0.50 € recovered when
unsold (fictitious data):

```python
from smerisk import newsvendor

sales = [42, 38, 51, 45, 40, 47, 39, 55, 44, 41, 48, 36, 50, 43, 46, 52, 37, 49, 45, 44]
result = newsvendor(
    demand={"type": "empirical", "samples": sales},
    unit_price=6.5,
    unit_cost=2.2,
    salvage_value=0.5,
    current_quantity=50,
)
print(round(result.result.critical_ratio, 3))  # 0.717
print(result.result.optimal_quantity)  # 48
print(result.result.expected_profit)  # 180.9
print(result.result.comparison.expected_profit)  # 180.2: the usual 50 quiches
print(round(result.result.expected_leftover, 2))  # 4.25
```

## HTTP

`POST /v1/operations/newsvendor` takes the body below and returns the response envelope.

```json
{
  "demand": {"type": "normal", "mean": 100, "sd": 20},
  "unit_price": 10,
  "unit_cost": 4,
  "current_quantity": 100
}
```

The response uses the common `result`, `assumptions`, `warnings`, and `meta` envelope.

## Input

| Field | Type | Required | Unit / default |
| --- | --- | --- | --- |
| `demand` | distribution | Yes | Demand for the period, units |
| `unit_price` | number ≥ 0 | Yes | Selling price per unit, currency |
| `unit_cost` | number ≥ 0 | Yes | Purchase or production cost per unit, currency |
| `salvage_value` | number | No | Recovery value per unsold unit; default `0` |
| `shortage_penalty` | number | No | Additional shortage cost per unit; default `0` |
| `current_quantity` | number ≥ 0 | No | Current order quantity for comparison |

## Calculation

Underage and overage costs are:

```text
c_u = unit_price - unit_cost + shortage_penalty
c_o = unit_cost - salvage_value
critical_ratio = c_u / (c_u + c_o)
optimal_quantity = inverse_CDF(demand, critical_ratio)
```

This is the classical single-period newsvendor solution. For normally distributed demand, the optimal quantity is
`mean + z[critical_ratio] * standard_deviation`. Expected sales, leftovers, shortage, and profit are evaluated at
the rounded quantity: exactly over the samples of an `empirical` demand, otherwise by quadrature over 200,000
quantiles of the demand distribution. Negative demand values count as 0.

```text
expected_profit = price * sales + salvage * leftover - cost * quantity - penalty * shortage
```

When `unit_price + shortage_penalty` does not exceed `unit_cost`, every unit loses money and the optimal quantity
is 0.

Reference: Arrow, Harris, and Marschak, “Optimal Inventory Policy,” *Econometrica*, 1951.

## Output (`result`)

| Field | Meaning |
| --- | --- |
| `critical_ratio` | Target service fractile |
| `optimal_quantity` | Optimal quantity rounded up to a whole unit |
| `expected_sales` | Expected units sold at the optimal quantity |
| `expected_leftover` | Expected unsold units |
| `expected_shortage` | Expected unmet demand |
| `expected_profit` | Expected contribution after relevant costs, euros |
| `comparison` | Same measures for `current_quantity`, if supplied |

## Warnings and errors

- `422` (`InvalidInput`): a negative price, cost, salvage value, penalty, or current quantity, or an invalid
  demand distribution.
- `409` (`ComputationImpossible`, `NO_FINITE_OPTIMUM`): `salvage_value` is at least `unit_cost`, so unsold units cost
  nothing and the optimal quantity is unbounded.

## Limitations

The model covers one period, assumes a known demand distribution, and does not model replenishment lead times,
substitution, multi-period inventory, or capacity constraints. The standard formulation assumes non-negative
underage and overage costs.
