"""Admin authentication middleware.

Simple API-key based auth for admin routes. The key is loaded from the
ADMIN_API_KEY environment variable. Browsers authenticate via a login
form that sets an httpOnly cookie; API clients send the key in the
Authorization: Bearer header.

MCP routes (/mcp) are NOT protected — they are called by apt-legal-agent
inside the K8s cluster.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "admin_session"
# Paths that don't require authentication
_PUBLIC_PATHS = frozenset({"/admin/login", "/admin/login/"})


def _get_admin_key() -> str | None:
    return os.environ.get("ADMIN_API_KEY") or None


def _check_key(candidate: str) -> bool:
    expected = _get_admin_key()
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


class AdminAuthMiddleware:
    """ASGI middleware that gates access behind ADMIN_API_KEY."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive, send)
        path = request.url.path

        # Login page is always accessible
        if path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        # Check cookie first, then Authorization header
        cookie_key = request.cookies.get(COOKIE_NAME)
        if cookie_key and _check_key(cookie_key):
            await self.app(scope, receive, send)
            return

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if _check_key(token):
                await self.app(scope, receive, send)
                return

        # Not authenticated — return 401
        if "text/html" in request.headers.get("accept", ""):
            # Browser request: redirect to login
            response = Response(
                status_code=302,
                headers={"location": "/admin/login"},
            )
        else:
            response = JSONResponse(
                {"error": "UNAUTHORIZED", "message": "인증이 필요합니다."},
                status_code=401,
            )
        await response(scope, receive, send)


async def handle_login(request: Request) -> Response:
    """POST /admin/login — validate key and set session cookie."""
    if request.method == "GET":
        from .routes import serve_login_page
        return await serve_login_page(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "INVALID_REQUEST", "message": "JSON body required"},
            status_code=400,
        )
    key = body.get("key", "")
    if not _check_key(key):
        return JSONResponse(
            {"error": "INVALID_KEY", "message": "API 키가 올바르지 않습니다."},
            status_code=401,
        )
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        key,
        httponly=True,
        samesite="strict",
        max_age=86400,  # 24h
        path="/admin",
    )
    return response


async def handle_logout(request: Request) -> Response:
    """POST /admin/logout — clear session cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/admin")
    return response
