"""Sale, Product (§10.4)."""

from __future__ import annotations

import datetime as dt

from pydantic import Field

from smerisk.contracts._base import Record, Ref


class Sale(Record):
    """Sales of one site over one day, or one hour of the day, optionally for one product."""

    site_ref: Ref
    date: dt.date
    hour: int | None = Field(None, ge=0, le=23)
    product_ref: Ref | None = None
    quantity: float = Field(ge=0)
    tickets: int | None = Field(None, ge=0)
    """Receipts issued: the arrivals at the counter."""
    covers: int | None = Field(None, ge=0)
    revenue_excl_tax: float
    """Can be negative when refunds exceed sales."""


class Product(Record):
    product_ref: Ref
    unit_price: float = Field(ge=0)
    unit_cost: float = Field(ge=0)
    salvage_value: float | None = Field(None, ge=0)
    """Value of an unsold unit (sold at a discount, given away: 0)."""
    current_order_quantity: float | None = Field(None, ge=0)
