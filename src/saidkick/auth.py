"""Shared-token authentication for the daemon.

This is deliberately the simplest thing that closes the hole: one shared bearer
token, checked in constant time, on every surface except ``/health``.

What it protects against: anything that can reach the port driving a browser
that holds your real logged-in profiles. That is a credential-exfiltration
surface the moment the daemon is reachable off-box, which is precisely the
deployment this architecture was chosen to allow.

What it is NOT: multi-user auth, per-agent authorization, or a permission
model. Every holder of the token can do everything.

Auth is daemon-only. No engine-layer module may import this.
"""

import logging
import os
import secrets
from hmac import compare_digest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings, home

log = logging.getLogger("saidkick.auth")

TOKEN_FILE = "token"
HEADER = "X-Saidkick-Token"
COOKIE = "saidkick_token"
#: Liveness probes must work without a credential.
OPEN_PATHS = frozenset({"/health"})


def resolve_token(settings: Settings) -> str | None:
    """The token to enforce: explicit > env > token file > freshly generated."""
    if not settings.require_auth:
        return None
    if settings.token:
        return settings.token
    env = os.environ.get("SAIDKICK_TOKEN")
    if env:
        return env

    path: Path = home() / TOKEN_FILE
    if path.is_file():
        existing = path.read_text().strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    path.chmod(0o600)
    log.warning("generated a new saidkick token at %s", path)
    return token


def presented_token(request: Request) -> str | None:
    """Pull a credential out of any form a caller can manage.

    Query and cookie are not laziness: the cockpit is a browser page and
    WebSockets cannot set request headers, so a header-only scheme would leave
    both unauthenticatable.
    """
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    direct = request.headers.get(HEADER)
    if direct:
        return direct.strip()
    query = request.query_params.get("token")
    if query:
        return query.strip()
    return request.cookies.get(COOKIE)


def token_ok(presented: str | None, expected: str | None) -> bool:
    if expected is None:
        return True
    if not presented:
        return False
    # Constant time: a plain == leaks the token one byte at a time under timing.
    return compare_digest(presented, expected)


def install(app: FastAPI, token: str | None) -> None:
    """Enforce the token on every HTTP route except OPEN_PATHS."""
    if token is None:
        return

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        presented = presented_token(request)
        if not token_ok(presented, token):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "missing or invalid saidkick token"},
            )
        response = await call_next(request)
        # A ?token= on a page load becomes a cookie so the cockpit keeps working
        # as the user clicks around without the token trailing every URL.
        if request.query_params.get("token") and "text/html" in response.headers.get(
            "content-type", ""
        ):
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return response


def ws_authorized(websocket, token: str | None) -> bool:
    """WebSockets cannot set headers, so they authenticate by query or cookie."""
    if token is None:
        return True
    presented = websocket.query_params.get("token") or websocket.cookies.get(COOKIE)
    return token_ok(presented, token)
