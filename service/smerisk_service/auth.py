"""Verify service-to-service tokens."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from smerisk_service.settings import Settings

ANONYMOUS = "anonymous"
_bearer = HTTPBearer(auto_error=False, description="One token per calling application.")


def caller_id(token: str) -> str:
    """Opaque identifier of a caller: jobs and datasets are isolated by it. The token itself is never stored."""
    return hashlib.sha256(token.encode()).hexdigest()[:24]


def caller(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    settings: Settings = request.app.state.settings
    if credentials is not None:
        token = credentials.credentials.encode()
        if any(hmac.compare_digest(token, t.get_secret_value().encode()) for t in settings.api_tokens):
            request.state.caller = caller_id(credentials.credentials)
            return str(request.state.caller)
        raise HTTPException(401, "unknown token", headers={"WWW-Authenticate": "Bearer"})
    if settings.allow_anonymous:
        request.state.caller = ANONYMOUS
        return ANONYMOUS
    raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
