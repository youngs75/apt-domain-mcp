"""apt-domain-mcp Phase 0 placeholder server.

Exposes a minimal MCP surface so that MCP Inspector and portal health checks
succeed before real database-backed tools land in Phase 1. The tools below
return hardcoded synthetic responses (한빛마을 새솔아파트) so the transport
path, tool discovery, and resource URI handling can all be verified end-to-end
without Postgres/Milvus connectivity.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from apt_domain_mcp import __version__

logger = logging.getLogger(__name__)

SERVER_NAME = "apt-domain-mcp"

# --- Phase 0 synthetic data (keeps server runnable without a database) ------
SYNTHETIC_COMPLEX_ID = "01HXXSOL0000000000000000AA"
SYNTHETIC_COMPLEX = {
    "complex_id": SYNTHETIC_COMPLEX_ID,
    "name": "한빛마을 새솔아파트",
    "address": "경기도 수원시 영통구 광교중앙로 999",
    "sido": "경기도",
    "sigungu": "수원시 영통구",
    "units": 1204,
    "buildings": 15,
    "max_floors": 25,
    "use_approval_date": "2008-09-15",
    "management_type": "위탁관리",
    "heating_type": "지역난방",
    "parking_slots": 1520,
    "external_ids": {"kapt_code": "A99999999"},
    "note": "Phase 0 합성 단지. 실제 DB 인제스트는 Phase 1에서 진행.",
}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_mcp() -> FastMCP:
    # DNS rebinding protection defaults to loopback-only Host headers which
    # breaks traffic behind the portal reverse proxy (same lesson learned in
    # kor-legal-mcp). Disable it explicitly.
    mcp = FastMCP(
        SERVER_NAME,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

    @mcp.tool(
        name="list_complexes",
        description=(
            "서버가 서빙 중인 단지 목록을 반환합니다. "
            "Phase 0에서는 합성 단지 1건만 하드코딩되어 있습니다."
        ),
    )
    async def _list_complexes() -> str:
        return _json_dump(
            {
                "phase": 0,
                "complexes": [SYNTHETIC_COMPLEX],
                "count": 1,
            }
        )

    @mcp.tool(
        name="get_complex_info",
        description=(
            "단지의 기본 메타데이터를 조회합니다. "
            "complex_id는 내부 ULID입니다. Phase 0에서는 합성 단지 1건만 지원합니다."
        ),
    )
    async def _get_complex_info(complex_id: str) -> str:
        if complex_id != SYNTHETIC_COMPLEX_ID:
            return _json_dump(
                {
                    "error": "COMPLEX_NOT_FOUND",
                    "message": (
                        f"Phase 0 파일럿은 단지 1건만 지원합니다. "
                        f"alias는 '{SYNTHETIC_COMPLEX_ID}' 입니다."
                    ),
                }
            )
        return _json_dump(SYNTHETIC_COMPLEX)

    @mcp.resource("apt-domain://complex/{complex_id}")
    async def _complex_resource(complex_id: str) -> str:
        if complex_id != SYNTHETIC_COMPLEX_ID:
            return f"[오류] 알 수 없는 complex_id: {complex_id}"
        return _json_dump(SYNTHETIC_COMPLEX)

    return mcp


async def _healthz(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "phase": 0,
            "components": {
                "postgres": "not_configured",
                "milvus": "not_configured",
            },
        }
    )


async def _root(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "name": SERVER_NAME,
            "version": __version__,
            "description": "공동주택 도메인 지식(관리규약·회의록·운영산출물) MCP 서버",
            "phase": 0,
            "endpoints": {"mcp": "/mcp", "health": "/healthz"},
        }
    )


_MCP = build_mcp()
_INNER_MCP_APP = _MCP.streamable_http_app()


@asynccontextmanager
async def _lifespan(app: Starlette):
    # Delegate to the inner FastMCP app's lifespan first — otherwise the /mcp
    # route raises "Task group is not initialized" on every request. Same
    # pattern as kor-legal-mcp.
    async with _INNER_MCP_APP.router.lifespan_context(app):
        yield


app = Starlette(
    routes=[
        Route("/", _root),
        Route("/healthz", _healthz),
        # streamable_http_app() exposes /mcp internally; mount at root to avoid
        # double-prefixing (which would cause a 307 redirect and break
        # Inspector).
        Mount("/", app=_INNER_MCP_APP),
    ],
    lifespan=_lifespan,
)
