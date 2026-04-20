"""apt-domain-mcp Phase 1 server.

DB-backed MCP tools (8 total: 2 admin + 6 query). All query tools enforce
tenant isolation via `complex_id` in the SQL WHERE clause. If DATABASE_URL is
not set the server still boots — all query tools return DB_NOT_CONFIGURED.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from apt_domain_mcp.admin.api import api_routes as _admin_api_routes
from apt_domain_mcp.admin.auth import AdminApiKeyMiddleware

from apt_domain_mcp import __version__, db
from apt_domain_mcp.tools import handlers as h

logger = logging.getLogger(__name__)

SERVER_NAME = "apt-domain-mcp"


def _load_dotenv() -> None:
    """Best-effort .env loader (only for local dev). Portal uses real env vars."""
    candidates = [Path.cwd() / ".env"]
    try:
        candidates.append(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
        break


_load_dotenv()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        SERVER_NAME,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

    # ---------------- admin / discovery ----------------
    @mcp.tool(
        name="list_complexes",
        description="서버가 서빙 중인 단지 목록. 단지별 기본 메타데이터 반환.",
    )
    async def _list_complexes() -> str:
        return _json_dump(await h.list_complexes())

    @mcp.tool(
        name="get_complex_info",
        description=(
            "단지의 상세 메타데이터(세대수·관리방식·현행 관리규약 버전·회의록 수)를 반환합니다. "
            "complex_id는 list_complexes에서 얻은 내부 ULID."
        ),
    )
    async def _get_complex_info(complex_id: str) -> str:
        return _json_dump(await h.get_complex_info(complex_id))

    # ---------------- regulation ----------------
    @mcp.tool(
        name="search_regulation",
        description=(
            "관리규약 조문을 검색합니다. complex_id 필수. query(키워드, body/title ILIKE) "
            "또는 category(카테고리 정확 매치) 중 최소 하나를 제공해야 하며 둘 다 병용 가능. "
            "version 미지정 시 현행(is_current) 버전 대상. "
            "category 예시: '주차', '반려동물', '관리비', '층간소음'."
        ),
    )
    async def _search_regulation(
        complex_id: str,
        query: str | None = None,
        category: str | None = None,
        version: int | None = None,
        limit: int = 10,
    ) -> str:
        return _json_dump(
            await h.search_regulation(
                complex_id, query, category=category, version=version, limit=limit
            )
        )

    @mcp.tool(
        name="get_regulation_article",
        description=(
            "특정 조문 전문을 반환합니다. include_history=true(기본)이면 "
            "해당 조문의 개정 이력(from/to 버전·변경 유형·개정 사유)도 함께 반환."
        ),
    )
    async def _get_regulation_article(
        complex_id: str,
        article_number: str,
        version: int | None = None,
        include_history: bool = True,
    ) -> str:
        return _json_dump(
            await h.get_regulation_article(
                complex_id,
                article_number,
                version=version,
                include_history=include_history,
            )
        )

    @mcp.tool(
        name="list_regulation_revisions",
        description="관리규약 버전 목록 및 조문 단위 개정 이력 요약.",
    )
    async def _list_regulation_revisions(complex_id: str) -> str:
        return _json_dump(await h.list_regulation_revisions(complex_id))

    # ---------------- meetings ----------------
    @mcp.tool(
        name="search_meeting_decisions",
        description=(
            "입주자대표회의 결정사항을 검색합니다. query(자유 검색어) 또는 "
            "category/result/date_from/date_to 필터로 조건 조합 가능. "
            "result는 '가결'|'부결'|'보류'. 날짜는 ISO 형식 (YYYY-MM-DD)."
        ),
    )
    async def _search_meeting_decisions(
        complex_id: str,
        query: str | None = None,
        category: str | None = None,
        result: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> str:
        return _json_dump(
            await h.search_meeting_decisions(
                complex_id,
                query,
                category=category,
                result=result,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        )

    @mcp.tool(
        name="get_meeting_detail",
        description="특정 회의록의 메타·안건·결정·원문 전체를 반환합니다.",
    )
    async def _get_meeting_detail(complex_id: str, meeting_id: str) -> str:
        return _json_dump(await h.get_meeting_detail(complex_id, meeting_id))

    # ---------------- wiki ----------------
    @mcp.tool(
        name="get_wiki_page",
        description=(
            "토픽별 LLM 큐레이션 위키 페이지를 반환합니다. "
            "Phase 1 후반 Wiki 생성기 도입 전에는 WIKI_NOT_FOUND 반환."
        ),
    )
    async def _get_wiki_page(complex_id: str, topic: str) -> str:
        return _json_dump(await h.get_wiki_page(complex_id, topic))

    # ---------------- resource ----------------
    @mcp.resource("apt-domain://complex/{complex_id}")
    async def _complex_resource(complex_id: str) -> str:
        return _json_dump(await h.get_complex_info(complex_id))

    return mcp


async def _healthz(_request: Request) -> JSONResponse:
    pool = db.get_pool()
    pg_status = "unknown"
    if pool is None:
        pg_status = "not_configured"
    else:
        try:
            async with db.acquire() as conn:
                await conn.fetchval("SELECT 1")
            pg_status = "ok"
        except Exception as e:
            pg_status = f"error: {type(e).__name__}"
    return JSONResponse(
        {
            "status": "ok" if pg_status in ("ok", "not_configured") else "degraded",
            "version": __version__,
            "phase": 1,
            "components": {"postgres": pg_status, "milvus": "not_configured"},
        }
    )


async def _root(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "name": SERVER_NAME,
            "version": __version__,
            "description": "공동주택 도메인 지식(관리규약·회의록·운영산출물) MCP 서버",
            "phase": 1,
            "endpoints": {"mcp": "/mcp", "health": "/healthz"},
        }
    )


_MCP = build_mcp()
_INNER_MCP_APP = _MCP.streamable_http_app()

_ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

_admin_api_app = Starlette(
    routes=_admin_api_routes,
    middleware=[Middleware(AdminApiKeyMiddleware, api_key=_ADMIN_API_KEY)],
)


@asynccontextmanager
async def _lifespan(app: Starlette):
    # Initialize DB pool before the inner MCP lifespan starts. The pool is
    # lazy: missing DATABASE_URL leaves it at None and query tools return
    # DB_NOT_CONFIGURED.
    try:
        await db.init_pool()
        if db.get_pool() is not None:
            logger.info("DB pool initialized")
        else:
            logger.warning("DATABASE_URL not set — query tools will return DB_NOT_CONFIGURED")
    except Exception as e:
        logger.exception("DB pool init failed: %s", e)

    async with _INNER_MCP_APP.router.lifespan_context(app):
        try:
            yield
        finally:
            await db.close_pool()


app = Starlette(
    routes=[
        Route("/", _root),
        Route("/healthz", _healthz),
        Mount("/admin/api", app=_admin_api_app),
        Mount("/", app=_INNER_MCP_APP),
    ],
    lifespan=_lifespan,
)
