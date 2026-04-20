"""API Key-based auth for /admin/api.

Checks X-Admin-API-Key header against ADMIN_API_KEY env.
"""
from __future__ import annotations

import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class AdminApiKeyMiddleware:
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        provided = headers.get("x-admin-api-key", "")
        if not self.api_key or not secrets.compare_digest(provided, self.api_key):
            response = JSONResponse(
                {"error": "UNAUTHORIZED",
                 "message": "Invalid or missing X-Admin-API-Key"},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
