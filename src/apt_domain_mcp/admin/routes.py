"""Admin route definitions and static file serving."""
from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .api import api_routes
from .auth import handle_login, handle_logout

_STATIC_DIR = Path(__file__).parent / "static"


async def serve_login_page(request: Request) -> Response:
    return FileResponse(_STATIC_DIR / "login.html", media_type="text/html")


async def serve_admin_page(request: Request) -> Response:
    return FileResponse(_STATIC_DIR / "admin.html", media_type="text/html")


# Routes mounted under /admin (auth middleware wraps everything except login)
admin_routes: list[Route] = [
    Route("/login", handle_login, methods=["GET", "POST"]),
    Route("/logout", handle_logout, methods=["POST"]),
    Route("/", serve_admin_page),
    *api_routes,
]
