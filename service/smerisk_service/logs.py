"""Structured logs: one event per request, job, and dataset operation, never any input data.

Each event has a name (``request``, ``job.finished``, ``dataset.appended``…) and fields: identifiers, sizes, counts,
durations, statuses. Request bodies, records, and exception messages are never written: they could carry business
data. Every response carries an ``X-Request-ID`` header, also found in the logs and in the ``trace_id`` of a 500.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("smerisk_service")
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    rid = request_id.get()
    log.log(level, name, extra={"fields": {"request_id": rid, **fields} if rid else fields})


def internal_error(exc: BaseException) -> str:
    """Log an unexpected error with its type and the place it happened, not its message; return the trace id."""
    trace_id = request_id.get() or uuid.uuid4().hex
    frames = traceback.extract_tb(exc.__traceback__)[-6:]
    event(
        "internal_error",
        logging.ERROR,
        trace_id=trace_id,
        exception=type(exc).__name__,
        where=[f"{f.filename.rsplit('site-packages', 1)[-1]}:{f.lineno} in {f.name}" for f in frames],
    )
    return trace_id


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        return json.dumps(line, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(f"{k}={v}" for k, v in getattr(record, "fields", {}).items())
        stamp = dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"{stamp} {record.levelname:<7} {record.getMessage()} {fields}".rstrip()


class _StandardOutput(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to the current ``sys.stdout``, even if it was replaced after start (test capture, reloaders)."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure(level: str, fmt: str) -> None:
    """Send the service's events to standard output (idempotent: a new app replaces the handler)."""
    handler = _StandardOutput()
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    log.handlers = [handler]
    log.setLevel(level)
    log.propagate = False


class RequestLog:
    """ASGI middleware: request id, then one ``request`` event with route, status, sizes, duration, and caller."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        given = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = given if _VALID_ID.match(given) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        status, sent, started_response = 500, 0, False

        async def send_with_id(message: Message) -> None:
            nonlocal status, sent, started_response
            if message["type"] == "http.response.start":
                started_response = True
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            # Handled here rather than by Starlette's last-resort handler, which runs outside this middleware and
            # would not know the request id: the trace id of a 500 is the request id.
            if started_response:
                raise
            body = json.dumps({"error": "INTERNAL_ERROR", "trace_id": internal_error(exc)}).encode()
            await send_with_id(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send_with_id({"type": "http.response.body", "body": body})
        finally:
            route = scope.get("route")
            state = scope.get("state", {})
            event(
                "request",
                logging.WARNING if status >= 500 else logging.INFO,
                method=scope["method"],
                route=getattr(route, "path", None) or scope["path"],
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                request_bytes=int(headers.get(b"content-length", b"0") or 0),
                response_bytes=sent,
                caller=state.get("caller"),
            )
            request_id.reset(token)
