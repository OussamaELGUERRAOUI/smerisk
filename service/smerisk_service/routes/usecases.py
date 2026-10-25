"""Business-level routes: /v1/usecases/… (§12)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, create_model

from smerisk import usecases
from smerisk_service.datasets import DatasetStore
from smerisk_service.routes import Compute, add_route, always, models, never

router = APIRouter(prefix="/v1/usecases", tags=["usecases"])

ASYNC_USECASES = {"payroll_at_risk", "cash_at_risk", "fixed_price_risk", "utilization_forecast"}
"""The use cases that simulate answer as jobs (§12)."""


def _with_dataset(func: Callable[..., Any]) -> tuple[type[BaseModel], Callable[[Request, BaseModel, str], Compute]]:
    """Request model = the use case's input plus ``dataset_id``; data sent in the body wins over the dataset."""
    model, _ = models(func)
    request_model = create_model(
        f"{model.__name__.removesuffix('Input')}Request",
        __base__=model,
        dataset_id=(
            str | None,
            Field(None, pattern=r"^[0-9a-f]{32}$", description="Raw data stored with POST /v1/datasets (§12.5)"),
        ),
    )
    contracts = set(model.model_fields) - {"settings"}

    def prepare(request: Request, body: BaseModel, who: str) -> Compute:
        data = body.model_dump(exclude={"dataset_id"}, exclude_unset=True)
        dataset_id = getattr(body, "dataset_id", None)
        if dataset_id:
            store: DatasetStore = request.app.state.datasets
            for name, rows in store.get(who, dataset_id).items():
                if name in contracts and not data.get(name):
                    data[name] = rows
        return lambda: func(data)

    return request_model, prepare


for name in usecases.__all__:
    func = getattr(usecases, name)
    request_model, prepare = _with_dataset(func)
    add_route(
        router,
        "/" + name.replace("_", "-"),
        func,
        run_async=always if name in ASYNC_USECASES else never,
        prepare=prepare,
        body_model=request_model,
        summary=(sys.modules[func.__module__].__doc__ or name).splitlines()[0]
        + (" Answered as a job." if name in ASYNC_USECASES else ""),
    )
