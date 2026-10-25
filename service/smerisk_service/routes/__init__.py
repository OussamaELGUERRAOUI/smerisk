"""HTTP routes under /v1.

Each route calls one public function of ``smerisk``. Its request and response models are read from the function's
annotations, so the OpenAPI contract follows the library without being written twice.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from typing import Annotated, Any, Union

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from smerisk_service.auth import caller
from smerisk_service.errors import ERROR_RESPONSES
from smerisk_service.jobs import JobAccepted, JobStore

Compute = Callable[[], BaseModel]
AsyncRule = Callable[[BaseModel], bool]


def never(_: BaseModel) -> bool:
    return False


def always(_: BaseModel) -> bool:
    return True


def models(func: Callable[..., Any]) -> tuple[type[BaseModel], Any]:
    """Input model (the model in ``params: Model | Mapping | None``) and response type of a library function."""
    hints = typing.get_type_hints(func)
    params = hints["params"]
    options = typing.get_args(params) if typing.get_origin(params) in (Union, types.UnionType) else (params,)
    model = next(o for o in options if isinstance(o, type) and issubclass(o, BaseModel))
    return model, hints["return"]


def add_route(
    router: APIRouter,
    path: str,
    func: Callable[..., Any],
    *,
    run_async: AsyncRule = never,
    prepare: Callable[[Request, BaseModel, str], Compute] | None = None,
    body_model: type[BaseModel] | None = None,
    summary: str | None = None,
) -> None:
    """POST ``path``: validate the body, then call ``func`` now (200) or as a job (202, §2.3).

    ``prepare`` turns the validated body into the call (the use cases merge a dataset); by default ``func(body)``.
    """
    model, result = models(func)
    body_type = body_model or model

    def endpoint(request: Request, body: BaseModel, who: str) -> Any:
        compute = prepare(request, body, who) if prepare else (lambda: func(body))
        if run_async(body):
            jobs: JobStore = request.app.state.jobs
            job = jobs.submit(who, compute, label=router.prefix + path)
            url = str(request.url_for("get_job", job_id=job.id).path)
            return JSONResponse(
                status_code=202,
                content=JobAccepted(job_id=job.id, status_url=url).model_dump(),
                headers={"Location": url},
            )
        return compute()

    endpoint.__name__ = path.strip("/").replace("/", "_").replace("-", "_")
    endpoint.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
            inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Annotated[body_type, Body()]),
            inspect.Parameter(
                "who", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Annotated[str, Depends(caller)]
            ),
        ]
    )
    asynchronous = run_async is always
    router.add_api_route(
        path,
        endpoint,
        methods=["POST"],
        response_model=None if asynchronous else result,
        status_code=202 if asynchronous else 200,
        responses={**ERROR_RESPONSES, 202: {"model": JobAccepted, "description": "Accepted as a job"}},
        summary=summary or (func.__doc__ or func.__name__).strip().splitlines()[0],
    )
