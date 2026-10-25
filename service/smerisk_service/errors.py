"""Error responses (§2.2): 422 with the fields in error, 413 with the limit, 409 with a business code, 500 with a trace
identifier and never any input data."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from smerisk import ComputationImpossible, InputTooLarge, InvalidInput
from smerisk_service import logs


class FieldErrorBody(BaseModel):
    path: list[str | int]
    message: str


class InvalidInputBody(BaseModel):
    errors: list[FieldErrorBody]


class TooLargeBody(BaseModel):
    error: str = "INPUT_TOO_LARGE"
    field: str
    size: int
    limit: int


class ImpossibleBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]


class InternalErrorBody(BaseModel):
    error: str = "INTERNAL_ERROR"
    trace_id: str


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or unknown bearer token"},
    409: {"model": ImpossibleBody, "description": "Computation impossible with this data"},
    413: {"model": TooLargeBody, "description": "Input above the limits"},
    422: {"model": InvalidInputBody, "description": "Invalid input"},
    500: {"model": InternalErrorBody, "description": "Internal error"},
}


def error_body(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """HTTP status and body of an exception, also used for failed jobs."""
    if isinstance(exc, InvalidInput):
        return 422, {"errors": [e.to_dict() for e in exc.errors]}
    if isinstance(exc, InputTooLarge):
        return 413, TooLargeBody(field=exc.field, size=exc.size, limit=exc.limit).model_dump()
    if isinstance(exc, ComputationImpossible):
        message = str(exc).removeprefix(f"{exc.code}: ")
        return 409, {"code": exc.code, "message": message, "details": exc.details}
    # The exception type and place only: the message of an unexpected error could quote input data.
    return 500, {"error": "INTERNAL_ERROR", "trace_id": logs.internal_error(exc)}


def _request_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = []
    for e in exc.errors():
        loc = list(e.get("loc", ()))
        if loc and loc[0] in ("body", "query", "path", "header"):
            loc = loc[1:]
        errors.append({"path": loc, "message": e.get("msg", "invalid value")})
    return JSONResponse(status_code=422, content={"errors": errors})


def _library_error(request: Request, exc: Exception) -> JSONResponse:
    status, body = error_body(exc)
    return JSONResponse(status_code=status, content=body)


def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)


def install(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _request_validation)
    app.add_exception_handler(HTTPException, _http_error)
    for error in (InvalidInput, InputTooLarge, ComputationImpossible, Exception):
        app.add_exception_handler(error, _library_error)
