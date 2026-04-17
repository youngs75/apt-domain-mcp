"""Admin REST API handlers.

All endpoints require authentication (enforced by AdminAuthMiddleware).
Mounted under /admin/api/*.
"""
from __future__ import annotations

import json
import logging
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
    return _json({"error": code, "message": message}, status=status)


# --------------------------------------------------------------------------
# GET /admin/api/complexes
# --------------------------------------------------------------------------
async def list_complexes(request: Request) -> JSONResponse:
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL not set", 503)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT complex_id, name, address, units, buildings, management_type
            FROM complex ORDER BY name
            """
        )
    return _json({
        "complexes": [dict(r) for r in rows],
    })


# --------------------------------------------------------------------------
# POST /admin/api/complexes
# --------------------------------------------------------------------------
async def create_complex(request: Request) -> JSONResponse:
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL not set", 503)
    try:
        body = await request.json()
    except Exception:
        return _err("INVALID_REQUEST", "JSON body required")

    required = ["complex_id", "name"]
    for field in required:
        if not body.get(field):
            return _err("INVALID_PARAMS", f"{field} is required")

    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO complex (complex_id, name, address, sido, sigungu,
                                 units, buildings, management_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (complex_id) DO UPDATE SET
                name = EXCLUDED.name,
                address = EXCLUDED.address,
                sido = EXCLUDED.sido,
                sigungu = EXCLUDED.sigungu,
                units = EXCLUDED.units,
                buildings = EXCLUDED.buildings,
                management_type = EXCLUDED.management_type
            """,
            body["complex_id"],
            body["name"],
            body.get("address", ""),
            body.get("sido", ""),
            body.get("sigungu", ""),
            body.get("units", 0),
            body.get("buildings", 0),
            body.get("management_type", ""),
        )
    return _json({"ok": True, "complex_id": body["complex_id"]}, 201)


# --------------------------------------------------------------------------
# GET /admin/api/complexes/{id}/regulations
# --------------------------------------------------------------------------
async def list_regulations(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL not set", 503)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT version, effective_date, is_current, summary,
                   (SELECT count(*) FROM regulation_article ra
                    WHERE ra.complex_id = rv.complex_id AND ra.version = rv.version)
                   AS article_count
            FROM regulation_version rv
            WHERE rv.complex_id = $1
            ORDER BY rv.version DESC
            """,
            complex_id,
        )
    return _json({
        "complex_id": complex_id,
        "versions": [dict(r) for r in rows],
    })


# --------------------------------------------------------------------------
# GET /admin/api/complexes/{id}/meetings
# --------------------------------------------------------------------------
async def list_meetings(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL not set", 503)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT meeting_id, meeting_date, meeting_type,
                   (SELECT count(*) FROM meeting_decision md
                    WHERE md.meeting_id = m.meeting_id)
                   AS decision_count
            FROM meeting m
            WHERE m.complex_id = $1
            ORDER BY m.meeting_date DESC
            """,
            complex_id,
        )
    return _json({
        "complex_id": complex_id,
        "meetings": [dict(r) for r in rows],
    })


# --------------------------------------------------------------------------
# GET /admin/api/complexes/{id}/documents
# --------------------------------------------------------------------------
async def list_documents(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL not set", 503)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT document_id, kind, title, source_path, sha256, created_at
            FROM document
            WHERE complex_id = $1
            ORDER BY created_at DESC
            """,
            complex_id,
        )
    return _json({
        "complex_id": complex_id,
        "documents": [dict(r) for r in rows],
    })


# --------------------------------------------------------------------------
# POST /admin/api/complexes/{id}/ingest
# --------------------------------------------------------------------------
async def ingest_file(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL not set", 503)

    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload:
            return _err("INVALID_PARAMS", "file field required")
        kind = form.get("kind", "regulation")
        make_current = form.get("make_current", "false").lower() in ("true", "1", "yes")
        file_bytes = await upload.read()
        file_content = file_bytes.decode("utf-8")
        filename = upload.filename or "upload.md"
    else:
        try:
            body = await request.json()
        except Exception:
            return _err("INVALID_REQUEST", "multipart/form-data or JSON body required")
        kind = body.get("kind", "regulation")
        file_content = body.get("content", "")
        filename = body.get("filename", "upload.md")
        make_current = body.get("make_current", False)
        if not file_content:
            return _err("INVALID_PARAMS", "content field required")

    if kind not in ("regulation", "regulation-diff", "meeting"):
        return _err("INVALID_PARAMS", f"Invalid kind: {kind}")

    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                result = await run_ingest(
                    conn,
                    complex_id=complex_id,
                    kind=kind,
                    file_content=file_content,
                    filename=filename,
                    make_current=make_current,
                )
    except Exception as exc:
        logger.exception("ingest failed: %s", exc)
        return _err("INGEST_ERROR", str(exc), 500)

    return _json({
        "ok": True,
        "kind": result.kind,
        "message": result.message,
        "count": result.count,
    })


# --------------------------------------------------------------------------
# Route list
# --------------------------------------------------------------------------
api_routes: list[Route] = [
    Route("/api/complexes", list_complexes, methods=["GET"]),
    Route("/api/complexes", create_complex, methods=["POST"]),
    Route("/api/complexes/{id}/regulations", list_regulations, methods=["GET"]),
    Route("/api/complexes/{id}/meetings", list_meetings, methods=["GET"]),
    Route("/api/complexes/{id}/documents", list_documents, methods=["GET"]),
    Route("/api/complexes/{id}/ingest", ingest_file, methods=["POST"]),
]
