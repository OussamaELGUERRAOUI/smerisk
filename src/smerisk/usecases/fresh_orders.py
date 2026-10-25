"""fresh-orders: quantity of each perishable product to prepare, from the demand of the same weekday (§12)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from smerisk.contracts import Product, Sale, parse_records
from smerisk.core.distributions import Distribution
from smerisk.core.envelope import InputModel, Warning, parse_input
from smerisk.formulas.operations import NewsvendorResult, newsvendor
from smerisk.prepare import DerivedParameters, QualityReport, daily_demand, demand_distribution
from smerisk.prepare.demand import DEMAND_WEEKS, MIN_DEMAND_DAYS
from smerisk.usecases._base import Records, Settings, UsecaseResult, formula_warnings, require, respond

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class FreshOrdersSettings(Settings):
    weekday: int | None = Field(None, ge=0, le=6)
    """Day to prepare (0 = Monday); by default every day of the week."""
    site_ref: str | None = None
    """Restrict the sales to one site; by default all sites together."""


class FreshOrdersInput(InputModel):
    sales: Records = Field(default_factory=list)
    """Sales by product (``product_ref`` set)."""
    products: Records = Field(default_factory=list)
    settings: FreshOrdersSettings = Field(default_factory=FreshOrdersSettings)


class FreshOrder(BaseModel):
    product_ref: str
    weekday: int
    order: NewsvendorResult


class FreshOrdersResult(BaseModel):
    orders: list[FreshOrder]
    """One recommendation per product and weekday with sales."""


def fresh_orders(
    params: FreshOrdersInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[FreshOrdersResult]:
    started = time.perf_counter()
    inp = parse_input(FreshOrdersInput, params, fields)
    settings = inp.settings
    parsed_sales = parse_records(Sale, inp.sales, name="sales")
    parsed_products = parse_records(Product, inp.products, name="products")
    sales = [s for s in parsed_sales.records if s.date <= settings.as_of]
    require("sales", len(sales))
    require("products", len(parsed_products.records))
    quality = QualityReport()
    quality.records("sales", parsed_sales, (s.date.isoformat() for s in sales))
    quality.records("products", parsed_products)

    derived = DerivedParameters()
    warnings: list[Warning] = []
    weekdays = [settings.weekday] if settings.weekday is not None else list(range(7))
    planned: list[tuple[Product, int, str]] = []
    for product in parsed_products.records:
        for weekday in weekdays:
            name = f"demand.{product.product_ref}.{WEEKDAYS[weekday]}"
            demand = daily_demand(
                sales, product.product_ref, settings.as_of, weekday=weekday, site_ref=settings.site_ref
            )
            fitted = demand_distribution(demand, name=name)
            quality.require(name, MIN_DEMAND_DAYS, len(demand))
            if fitted is None:
                continue
            estimate, fit_warnings = fitted
            derived.add(name, estimate, Distribution)
            warnings.extend(fit_warnings)
            planned.append((product, weekday, name))
    require("sales", len(planned), f"no sale in the {DEMAND_WEEKS} weeks before as_of on the requested days")
    derived.apply(settings.overrides)

    orders = []
    assumptions: dict[str, Any] = {
        "history_weeks": DEMAND_WEEKS,
        "salvage_value": "products.salvage_value, otherwise 0",
        "shortage_penalty": 0,
        "demand": "sales of the same weekday; a sold-out day understates the demand",
    }
    for product, weekday, name in planned:
        result = newsvendor(
            demand=derived.value(name),
            unit_price=product.unit_price,
            unit_cost=product.unit_cost,
            salvage_value=product.salvage_value or 0,
            current_quantity=product.current_order_quantity,
        )
        warnings.extend(formula_warnings(result))
        orders.append(FreshOrder(product_ref=product.product_ref, weekday=weekday, order=result.result))
    return respond(
        FreshOrdersResult(orders=orders),
        method="fresh-orders",
        started=started,
        settings=settings,
        assumptions=assumptions,
        warnings=warnings,
        derived=derived,
        quality=quality,
    )
