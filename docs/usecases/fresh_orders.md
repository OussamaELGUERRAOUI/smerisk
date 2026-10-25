---
title: Perishable goods order quantities in Python
description: Recommend the units of each perishable product to prepare for a weekday from the sales of the same weekday over 12 weeks.
---

# Perishable goods order quantities in Python

Function: `fresh_orders`

## Purpose

Recommends how many units of each perishable product to prepare for a given weekday, from the sales of the same
weekday over the last 12 weeks. A bakery or a restaurant balances unsold units against missed sales. The use case
builds the demand distribution and calls [`newsvendor`](../formulas/newsvendor.md) for each product.

**Status:** implemented in the Python package (`smerisk.usecases`) and served by the [HTTP service](../service.md).

## Python

Quiches sold on the last 12 Mondays (fictitious), sold 6.50 €, cost 2.20 €, 0.50 € recovered when unsold:

```python
import datetime as dt

from smerisk.usecases import fresh_orders

mondays = [dt.date(2026, 10, 19) - dt.timedelta(weeks=w) for w in range(12)]
quiches = [42, 38, 51, 45, 40, 47, 39, 55, 44, 41, 48, 36]
sales = [
    {"site_ref": "bakery", "date": d.isoformat(), "product_ref": "quiche", "quantity": q, "revenue_excl_tax": q * 6.5}
    for d, q in zip(mondays, quiches)
]
products = [{"product_ref": "quiche", "unit_price": 6.5, "unit_cost": 2.2, "salvage_value": 0.5}]
out = fresh_orders(sales=sales, products=products, settings={"as_of": "2026-10-20", "weekday": 0})
order = out.result.orders[0].order
print(order.optimal_quantity, order.expected_profit)  # 47 176.6
```

## HTTP

`POST /v1/usecases/fresh-orders` takes the body below and returns the response envelope.

```json
{"sales": [{"site_ref": "bakery", "date": "2026-10-19", "product_ref": "quiche", "quantity": 42,
            "revenue_excl_tax": 273}],
 "products": [{"product_ref": "quiche", "unit_price": 6.5, "unit_cost": 2.2, "salvage_value": 0.5}],
 "settings": {"as_of": "2026-10-20", "weekday": 0}}
```

## Input

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `sales` | records | yes | — | sales by product (`product_ref`) |
| `products` | records | yes | — | price, cost, salvage value, current order quantity |
| `settings.as_of` | date | no | today | later sales are ignored |
| `settings.weekday` | 0 (Monday) to 6 | no | every day | day to prepare |
| `settings.site_ref` | string | no | all sites | restrict the sales to one site |
| `settings.overrides` | object | no | — | `demand.<product>.<weekday>` (a distribution) |

## Preparation

| Derived parameter | Source |
| --- | --- |
| `demand.<product>.<weekday>` | daily quantities of the same weekday over 12 weeks: `empirical` from 8 days, otherwise `normal` truncated at 0 |

A day on which the site sold something but not this product counts as 0.

## Output

`orders[]`: for each product and weekday with sales, `product_ref`, `weekday`, and the
[`newsvendor`](../formulas/newsvendor.md) `order` (optimal quantity, expected sales, leftovers, shortage, profit, and
the comparison with `current_order_quantity`).

## Warnings and data quality

- `data_quality.requirements`: 8 days per product and weekday.
- `SMALL_SAMPLE` when the demand is normal. 422 without sales or products, or without any sale on the requested days.

## Limitations

Sales are what was sold, not what was asked for: a day that sold out understates the demand, so the recommendation
is cautious for products that often sell out. Holidays and special days should be removed from the sales.
