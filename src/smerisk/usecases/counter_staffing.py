"""counter-staffing: agents needed at the counter for each site, weekday, and hour (§12)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from smerisk.contracts import Sale, parse_records
from smerisk.core.envelope import InputModel, parse_input
from smerisk.core.types import Level
from smerisk.formulas.operations import ErlangCResult, erlang_c
from smerisk.prepare import DerivedParameters, Estimate, QualityReport, arrival_rates
from smerisk.prepare.demand import ARRIVAL_WEEKS
from smerisk.usecases._base import Records, Settings, UsecaseResult, require, respond

MIN_DAYS = 4
"""Days observed per slot: below, one unusual day weighs a quarter of the estimate."""


class CounterStaffingSettings(Settings):
    service_time_minutes: float = Field(gt=0)
    """Mean time to serve one customer; required."""
    target_wait_seconds: float = Field(180, ge=0)
    target_service_level: Level = 0.8
    site_ref: str | None = None


class CounterStaffingInput(InputModel):
    sales: Records = Field(default_factory=list)
    """Sales with ``hour`` and ``tickets``."""
    settings: CounterStaffingSettings


class CounterSlot(BaseModel):
    site_ref: str
    weekday: int
    """0 = Monday."""
    hour: int
    arrival_rate: float
    irregular: bool
    """Busier and quieter days than a Poisson flow would give: plan a margin above the recommendation."""
    staffing: ErlangCResult | None
    """``None`` when no customer is expected in the slot."""


class CounterStaffingResult(BaseModel):
    slots: list[CounterSlot]


def counter_staffing(
    params: CounterStaffingInput | Mapping[str, Any] | None = None, /, **fields: Any
) -> UsecaseResult[CounterStaffingResult]:
    started = time.perf_counter()
    inp = parse_input(CounterStaffingInput, params, fields)
    settings = inp.settings
    parsed = parse_records(Sale, inp.sales, name="sales")
    sales = [s for s in parsed.records if s.date <= settings.as_of]
    quality = QualityReport()
    quality.records("sales", parsed, (s.date.isoformat() for s in sales))
    slots = arrival_rates(sales, settings.as_of, site_ref=settings.site_ref)
    require("sales", len(slots), f"no sale with hour and tickets in the {ARRIVAL_WEEKS} weeks before as_of")

    derived = DerivedParameters()
    for slot in slots:
        path = f"arrival_rate.{slot.site_ref}.{slot.weekday}.{slot.hour}"
        derived.add(
            path,
            Estimate(
                value=slot.arrival_rate,
                source="estimated",
                method=f"mean tickets in the hour over {slot.n_days} days",
                n_points=slot.n_days,
            ),
            float,
        )
        quality.require(path, MIN_DAYS, slot.n_days)
        if slot.irregular:
            quality.outlier("sales", path, slot.variance, slot.variance / slot.arrival_rate if slot.arrival_rate else 0)
    derived.apply(settings.overrides)

    result_slots = []
    assumptions: dict[str, Any] = {"history_weeks": ARRIVAL_WEEKS}
    for slot in slots:
        rate = derived.value(f"arrival_rate.{slot.site_ref}.{slot.weekday}.{slot.hour}")
        staffing = None
        if rate > 0:
            sized = erlang_c(
                arrival_rate=rate,
                service_time_minutes=settings.service_time_minutes,
                target_wait_seconds=settings.target_wait_seconds,
                target_service_level=settings.target_service_level,
            )
            staffing = sized.result
            assumptions["erlang_c"] = sized.assumptions
        result_slots.append(
            CounterSlot(
                site_ref=slot.site_ref,
                weekday=slot.weekday,
                hour=slot.hour,
                arrival_rate=rate,
                irregular=slot.irregular,
                staffing=staffing,
            )
        )
    return respond(
        CounterStaffingResult(slots=result_slots),
        method="counter-staffing",
        started=started,
        settings=settings,
        assumptions=assumptions,
        warnings=[],
        derived=derived,
        quality=quality,
    )
