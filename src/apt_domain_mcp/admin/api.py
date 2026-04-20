"""Admin REST API routes for apt-domain-mcp.

Mounted at /admin/api by the outer Starlette app. All paths here are relative
(e.g. "/complexes" becomes "/admin/api/complexes" externally).

Every response is JSON. Errors never return plain text or HTML.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import db
from ..ingest.service import IngestResult, run_ingest

logger = logging.getLogger(__name__)


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _err(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


async def _with_pool(fn):
    """Helper: return 503 if DB pool is not configured."""
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    return await fn()


# --------------------------------------------------------------------------
# GET /complexes
# --------------------------------------------------------------------------
async def list_complexes(request: Request) -> JSONResponse:
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT complex_id, name, address, sido, sigungu, units, buildings,
                       use_approval_date, management_type, external_ids
                FROM complex
                ORDER BY name
                """
            )
        complexes = [
            {
                "complex_id": r["complex_id"],
                "name": r["name"],
                "address": r["address"],
                "sido": r["sido"],
                "sigungu": r["sigungu"],
                "units": r["units"],
                "buildings": r["buildings"],
                "use_approval_date": r["use_approval_date"].isoformat() if r["use_approval_date"] else None,
                "management_type": r["management_type"],
                "external_ids": _json_loads(r["external_ids"]),
            }
            for r in rows
        ]
        return _json({"count": len(complexes), "complexes": complexes})
    except Exception:
        logger.exception("list_complexes failed")
        return _err("INTERNAL_ERROR", "단지 목록 조회 실패", 500)


# --------------------------------------------------------------------------
# POST /complexes
# --------------------------------------------------------------------------
async def create_complex(request: Request) -> JSONResponse:
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        body = await request.json()
    except Exception:
        return _err("INVALID_JSON", "요청 본문이 유효한 JSON이 아닙니다.")

    name = body.get("name")
    address = body.get("address")
    if not name or not address:
        return _err("INVALID_PARAMS", "name과 address는 필수입니다.")

    # complex_id auto-issue — server generates a ULID when omitted
    from ..ingest.repository import new_ulid_like, upsert_complex
    provided_id = body.get("complex_id")
    if provided_id:
        complex_id = provided_id
        generated = False
    else:
        complex_id = new_ulid_like()
        generated = True
    body["complex_id"] = complex_id

    try:
        async with db.acquire() as conn:
            await upsert_complex(conn, body)
        return _json(
            {
                "complex_id": complex_id,
                "generated": generated,
                "status": "created" if generated else "upserted",
            },
            201,
        )
    except Exception:
        logger.exception("create_complex failed")
        return _err("INTERNAL_ERROR", "단지 생성 실패", 500)


# --------------------------------------------------------------------------
# GET /complexes/{id}/regulations
# --------------------------------------------------------------------------
async def list_regulations(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        async with db.acquire() as conn:
            if not await _complex_exists(conn, complex_id):
                return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지: {complex_id}", 404)
            rows = await conn.fetch(
                """
                SELECT version, effective_date, summary, is_current, source_document
                FROM regulation_version
                WHERE complex_id = $1
                ORDER BY version
                """,
                complex_id,
            )
        return _json({
            "complex_id": complex_id,
            "count": len(rows),
            "versions": [
                {
                    "version": r["version"],
                    "effective_date": r["effective_date"].isoformat(),
                    "summary": r["summary"],
                    "is_current": r["is_current"],
                }
                for r in rows
            ],
        })
    except Exception:
        logger.exception("list_regulations failed")
        return _err("INTERNAL_ERROR", "관리규약 목록 조회 실패", 500)


# --------------------------------------------------------------------------
# GET /complexes/{id}/meetings
# --------------------------------------------------------------------------
async def list_meetings(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        async with db.acquire() as conn:
            if not await _complex_exists(conn, complex_id):
                return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지: {complex_id}", 404)
            rows = await conn.fetch(
                """
                SELECT meeting_id, meeting_date, meeting_type,
                       attendees_count, quorum, source_document
                FROM meeting
                WHERE complex_id = $1
                ORDER BY meeting_date DESC
                """,
                complex_id,
            )
        return _json({
            "complex_id": complex_id,
            "count": len(rows),
            "meetings": [
                {
                    "meeting_id": r["meeting_id"],
                    "meeting_date": r["meeting_date"].isoformat(),
                    "meeting_type": r["meeting_type"],
                    "attendees_count": r["attendees_count"],
                    "quorum": r["quorum"],
                }
                for r in rows
            ],
        })
    except Exception:
        logger.exception("list_meetings failed")
        return _err("INTERNAL_ERROR", "회의록 목록 조회 실패", 500)


# --------------------------------------------------------------------------
# GET /complexes/{id}/documents
# --------------------------------------------------------------------------
async def list_documents(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        async with db.acquire() as conn:
            if not await _complex_exists(conn, complex_id):
                return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지: {complex_id}", 404)
            rows = await conn.fetch(
                """
                SELECT document_id, kind, title, source_path, sha256, pages, created_at
                FROM document
                WHERE complex_id = $1
                ORDER BY created_at DESC
                """,
                complex_id,
            )
        return _json({
            "complex_id": complex_id,
            "count": len(rows),
            "documents": [
                {
                    "document_id": r["document_id"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "source_path": r["source_path"],
                    "sha256": r["sha256"],
                    "pages": r["pages"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        })
    except Exception:
        logger.exception("list_documents failed")
        return _err("INTERNAL_ERROR", "문서 목록 조회 실패", 500)


# --------------------------------------------------------------------------
# POST /complexes/{id}/ingest
# --------------------------------------------------------------------------
async def ingest(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)

    content_type = request.headers.get("content-type", "")

    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            kind = form.get("kind", "")
            make_current = form.get("make_current", "false").lower() in ("true", "1", "yes")
            upload = form.get("file")
            if not upload or not kind:
                return _err("INVALID_PARAMS", "kind와 file은 필수입니다 (multipart/form-data).")

            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="wb"
            ) as tmp:
                content = await upload.read()
                tmp.write(content)
                tmp_path = Path(tmp.name)
        elif "application/json" in content_type:
            body = await request.json()
            kind = body.get("kind", "")
            make_current = body.get("make_current", False)
            file_path = body.get("file_path")
            if not kind or not file_path:
                return _err("INVALID_PARAMS", "kind와 file_path는 필수입니다 (JSON).")
            tmp_path = Path(file_path)
            if not tmp_path.exists():
                return _err("FILE_NOT_FOUND", f"파일을 찾을 수 없습니다: {file_path}", 404)
        else:
            return _err("INVALID_CONTENT_TYPE",
                        "Content-Type은 multipart/form-data 또는 application/json이어야 합니다.")

        async with db.acquire() as conn:
            if not await _complex_exists(conn, complex_id):
                return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지: {complex_id}", 404)
            async with conn.transaction():
                result: IngestResult = await run_ingest(
                    conn,
                    complex_id=complex_id,
                    kind=kind,
                    file_path=tmp_path,
                    make_current=make_current,
                )

        if result.success:
            return _json({
                "status": "ok",
                "kind": result.kind,
                "message": result.message,
                "details": result.details,
            })
        else:
            return _err("INGEST_FAILED", result.message, 422)

    except Exception:
        logger.exception("ingest failed for complex_id=%s", complex_id)
        return _err("INTERNAL_ERROR", "인제스트 처리 실패", 500)


# --------------------------------------------------------------------------
# DELETE /complexes/{id}?confirm_name=<exact name>
# 단지 하드 삭제 — confirm_name이 DB의 name과 완전 일치해야 실행.
# --------------------------------------------------------------------------
async def delete_complex(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    confirm_name = request.query_params.get("confirm_name", "")
    if not confirm_name:
        return _err(
            "INVALID_PARAMS",
            "confirm_name 쿼리 파라미터는 필수입니다. 삭제할 단지 이름을 정확히 전달하세요.",
        )
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name FROM complex WHERE complex_id = $1",
                complex_id,
            )
            if row is None:
                return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지: {complex_id}", 404)
            if row["name"] != confirm_name:
                return _err(
                    "NAME_MISMATCH",
                    f"confirm_name이 단지 이름과 일치하지 않습니다. DB 값: {row['name']!r}",
                    409,
                )
            async with conn.transaction():
                # regulation_diff FK has no ON DELETE CASCADE, so its rows would
                # block the cascade chain from complex → regulation_version.
                # Clear them first, then let CASCADE handle the rest.
                # regulation_diff 의 FK에는 ON DELETE CASCADE 가 빠져 있어
                # complex → regulation_version CASCADE 체인을 막는다. 먼저 명시
                # 삭제 후, 나머지는 CASCADE 로 자동 정리된다.
                await conn.execute(
                    "DELETE FROM regulation_diff WHERE complex_id = $1",
                    complex_id,
                )
                await conn.execute(
                    "DELETE FROM complex WHERE complex_id = $1",
                    complex_id,
                )
        return _json({"complex_id": complex_id, "status": "deleted"})
    except Exception:
        logger.exception("delete_complex failed for %s", complex_id)
        return _err("INTERNAL_ERROR", "단지 삭제 실패", 500)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
async def _complex_exists(conn, complex_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM complex WHERE complex_id = $1", complex_id
    )
    return row is not None


def _json_loads(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Route table (relative paths — mounted under /admin/api)
# --------------------------------------------------------------------------
api_routes: list[Route] = [
    Route("/complexes", list_complexes, methods=["GET"]),
    Route("/complexes", create_complex, methods=["POST"]),
    Route("/complexes/{id}", delete_complex, methods=["DELETE"]),
    Route("/complexes/{id}/regulations", list_regulations, methods=["GET"]),
    Route("/complexes/{id}/meetings", list_meetings, methods=["GET"]),
    Route("/complexes/{id}/documents", list_documents, methods=["GET"]),
    Route("/complexes/{id}/ingest", ingest, methods=["POST"]),
]
