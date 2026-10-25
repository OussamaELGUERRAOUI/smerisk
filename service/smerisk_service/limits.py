"""Maximum input sizes (413) and quotas (§2.6).

The library checks its own limits (simulations, components, periods, observations) and raises 413; this module
rejects request bodies above ``max_body_bytes`` before the application parses them. Quotas of jobs and datasets per
caller are in ``JobStore`` and ``DatasetStore`` (429).
"""

from __future__ import annotations

import json
import zlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send


async def _read_body(receive: Receive, limit: int) -> tuple[bytes | None, int]:
    """The whole body, or ``None`` with the size reached once above ``limit`` (or when the client left)."""
    body, more = b"", True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            return None, 0
        body += message.get("body", b"")
        more = message.get("more_body", False)
        if len(body) > limit:
            return None, len(body)
    return body, len(body)


def _replay(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


async def _reject(send: Send, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class GzipRequest:
    """ASGI middleware: decompress a ``Content-Encoding: gzip`` body, up to ``limit`` bytes once decompressed.

    Decompression stops as soon as the limit is passed, so a small compressed bomb cannot fill the memory. Another
    encoding answers 415.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers", [])) if scope["type"] == "http" else {}
        encoding = headers.get(b"content-encoding", b"identity").decode("latin-1").strip().lower()
        if encoding in ("", "identity"):
            await self.app(scope, receive, send)
            return
        if encoding != "gzip":
            await _reject(send, 415, {"error": f"unsupported Content-Encoding: {encoding}; use gzip"})
            return
        compressed, _ = await _read_body(receive, 2**62)
        if compressed is None:
            return
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            body = inflater.decompress(compressed, self.limit + 1)
        except zlib.error:
            await _reject(send, 400, {"error": "the body is not valid gzip"})
            return
        if len(body) > self.limit or inflater.unconsumed_tail:
            await _reject(
                send,
                413,
                {"error": "INPUT_TOO_LARGE", "field": "body (decompressed)", "size": len(body), "limit": self.limit},
            )
            return
        rewritten = [(k, v) for k, v in scope["headers"] if k not in (b"content-encoding", b"content-length")]
        scope = {**scope, "headers": [*rewritten, (b"content-length", str(len(body)).encode())]}
        await self.app(scope, _replay(body, receive), send)


class BodySizeLimit:
    """ASGI middleware: 413 when the declared or received body exceeds ``limit`` bytes.

    A body without ``Content-Length`` (chunked) is read up to the limit before the application sees it.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            if declared.isdigit() and int(declared) > self.limit:
                await self._reject(send, int(declared))
            else:
                await self.app(scope, receive, send)
            return

        body, size = await _read_body(receive, self.limit)
        if body is None:
            if size:
                await self._reject(send, size)
            return
        await self.app(scope, _replay(body, receive), send)

    async def _reject(self, send: Send, size: int) -> None:
        await _reject(send, 413, {"error": "INPUT_TOO_LARGE", "field": "body", "size": size, "limit": self.limit})
