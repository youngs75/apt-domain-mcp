"""Admin REST API tests — no real DB required.

All DB calls are replaced with unittest.mock.AsyncMock so tests run offline.
The admin app is exercised via Starlette's TestClient (WSGI sync wrapper).

Coverage:
  - Auth middleware (401 on missing/wrong key)
  - GET  /complexes  — list, DB-not-configured
  - POST /complexes  — create with and without complex_id, validation errors
  - DELETE /complexes/{id} — happy path, 400/404/409 error paths, DB-not-configured
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from apt_domain_mcp.admin.api import api_routes
from apt_domain_mcp.admin.auth import AdminApiKeyMiddleware

API_KEY = "test-secret"


# ---------------------------------------------------------------------------
# App fixture — standalone admin app with a fixed test key
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    admin_app = Starlette(
        routes=api_routes,
        middleware=[Middleware(AdminApiKeyMiddleware, api_key=API_KEY)],
    )
    with TestClient(admin_app, raise_server_exceptions=True) as c:
        yield c


def auth(extra: dict | None = None) -> dict:
    headers = {"X-Admin-API-Key": API_KEY}
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def test_missing_api_key_returns_401(client):
    r = client.get("/complexes")
    assert r.status_code == 401
    assert r.json()["error"] == "UNAUTHORIZED"


def test_wrong_api_key_returns_401(client):
    r = client.get("/complexes", headers={"X-Admin-API-Key": "wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /complexes
# ---------------------------------------------------------------------------

def test_list_complexes_no_db_returns_503(client):
    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=None):
        r = client.get("/complexes", headers=auth())
    assert r.status_code == 503
    assert r.json()["error"] == "DB_NOT_CONFIGURED"


def test_list_complexes_returns_list(client):
    fake_row = {
        "complex_id": "01ABC",
        "name": "테스트 단지",
        "address": "서울시",
        "sido": "서울",
        "sigungu": "강남구",
        "units": 100,
        "buildings": 5,
        "use_approval_date": None,
        "management_type": "위탁",
        "external_ids": "{}",
    }
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[fake_row])
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=mock_pool), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        r = client.get("/complexes", headers=auth())

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["complexes"][0]["complex_id"] == "01ABC"


# ---------------------------------------------------------------------------
# POST /complexes
# ---------------------------------------------------------------------------

def test_create_complex_missing_name_returns_400(client):
    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()):
        r = client.post(
            "/complexes",
            headers=auth({"Content-Type": "application/json"}),
            content=json.dumps({"address": "서울시"}),
        )
    assert r.status_code == 400
    assert r.json()["error"] == "INVALID_PARAMS"


def test_create_complex_missing_address_returns_400(client):
    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()):
        r = client.post(
            "/complexes",
            headers=auth({"Content-Type": "application/json"}),
            content=json.dumps({"name": "테스트"}),
        )
    assert r.status_code == 400
    assert r.json()["error"] == "INVALID_PARAMS"


def test_create_complex_autogenerates_id(client):
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire, \
         patch("apt_domain_mcp.admin.api.upsert_complex", new_callable=AsyncMock) if False else \
             patch("apt_domain_mcp.ingest.repository.upsert_complex", new_callable=AsyncMock) as mock_upsert:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("apt_domain_mcp.ingest.repository.new_ulid_like", return_value="GENERATED_ID"):
            r = client.post(
                "/complexes",
                headers=auth({"Content-Type": "application/json"}),
                content=json.dumps({"name": "신규 단지", "address": "부산시"}),
            )

    assert r.status_code == 201
    body = r.json()
    assert body["generated"] is True
    assert body["status"] == "created"


def test_create_complex_with_provided_id_upserts(client):
    mock_conn = AsyncMock()

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire, \
         patch("apt_domain_mcp.ingest.repository.upsert_complex", new_callable=AsyncMock):
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        r = client.post(
            "/complexes",
            headers=auth({"Content-Type": "application/json"}),
            content=json.dumps({"complex_id": "FIXED_ID", "name": "고정 단지", "address": "인천시"}),
        )

    assert r.status_code == 201
    body = r.json()
    assert body["complex_id"] == "FIXED_ID"
    assert body["generated"] is False
    assert body["status"] == "upserted"


# ---------------------------------------------------------------------------
# DELETE /complexes/{id}
# ---------------------------------------------------------------------------

def test_delete_complex_no_db_returns_503(client):
    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=None):
        r = client.delete(
            "/complexes/SOME_ID?confirm_name=테스트",
            headers=auth(),
        )
    assert r.status_code == 503
    assert r.json()["error"] == "DB_NOT_CONFIGURED"


def test_delete_complex_missing_confirm_name_returns_400(client):
    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()):
        r = client.delete("/complexes/SOME_ID", headers=auth())
    assert r.status_code == 400
    assert r.json()["error"] == "INVALID_PARAMS"


def test_delete_complex_not_found_returns_404(client):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        r = client.delete(
            "/complexes/UNKNOWN?confirm_name=whatever",
            headers=auth(),
        )

    assert r.status_code == 404
    assert r.json()["error"] == "COMPLEX_NOT_FOUND"


def test_delete_complex_name_mismatch_returns_409(client):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"name": "실제 단지명"})

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        r = client.delete(
            "/complexes/SOME_ID?confirm_name=틀린이름",
            headers=auth(),
        )

    assert r.status_code == 409
    assert r.json()["error"] == "NAME_MISMATCH"


def test_delete_complex_success(client):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"name": "삭제할 단지"})
    mock_conn.execute = AsyncMock()

    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        r = client.delete(
            "/complexes/TARGET_ID?confirm_name=삭제할 단지",
            headers=auth(),
        )

    assert r.status_code == 200
    body = r.json()
    assert body["complex_id"] == "TARGET_ID"
    assert body["status"] == "deleted"

    calls = [str(c) for c in mock_conn.execute.call_args_list]
    assert any("complex" in c for c in calls)


# ---------------------------------------------------------------------------
# GET /complexes/{id}/documents
# ---------------------------------------------------------------------------

def test_list_documents_no_db_returns_503(client):
    # DB pool not configured — should short-circuit with 503
    # DB 풀 미설정 — 503 으로 즉시 반환
    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=None):
        r = client.get("/complexes/ANY_ID/documents", headers=auth())
    assert r.status_code == 503
    assert r.json()["error"] == "DB_NOT_CONFIGURED"


def test_list_documents_complex_not_found_returns_404(client):
    # _complex_exists returns False → 404 COMPLEX_NOT_FOUND
    # _complex_exists 가 False 반환 → 404 COMPLEX_NOT_FOUND
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        r = client.get("/complexes/UNKNOWN/documents", headers=auth())

    assert r.status_code == 404
    assert r.json()["error"] == "COMPLEX_NOT_FOUND"


def test_list_documents_returns_rows(client):
    # Happy path — rows are returned and uploaded_at is serialized under
    # the API-contract key `created_at`.
    # 정상 경로 — row 들이 반환되고 uploaded_at 은 API 계약 키 `created_at` 으로 직렬화.
    from datetime import datetime, timezone

    ts_newer = datetime(2026, 4, 22, 9, 0, tzinfo=timezone.utc)
    ts_older = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    fake_rows = [
        {
            "document_id": "DOC_NEW",
            "kind": "meeting",
            "title": "회의록 2026-04-22 (정기)",
            "source_path": "synthetic/meetings/2026-04-22_regular.md",
            "sha256": "beef",
            "pages": None,
            "uploaded_at": ts_newer,
        },
        {
            "document_id": "DOC_OLD",
            "kind": "regulation",
            "title": "관리규약 v1",
            "source_path": "synthetic/regulation_v1.md",
            "sha256": "cafe",
            "pages": 84,
            "uploaded_at": ts_older,
        },
    ]

    mock_conn = AsyncMock()
    # fetchrow used by _complex_exists — return a truthy row to signal existence
    # fetchrow 는 _complex_exists 가 사용 — 존재 신호로 truthy row 반환
    mock_conn.fetchrow = AsyncMock(return_value={"?column?": 1})
    mock_conn.fetch = AsyncMock(return_value=fake_rows)

    with patch("apt_domain_mcp.admin.api.db.get_pool", return_value=MagicMock()), \
         patch("apt_domain_mcp.admin.api.db.acquire") as mock_acquire:
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        r = client.get("/complexes/01ABC/documents", headers=auth())

    assert r.status_code == 200
    body = r.json()
    assert body["complex_id"] == "01ABC"
    assert body["count"] == 2
    assert body["documents"][0]["document_id"] == "DOC_NEW"
    assert body["documents"][0]["kind"] == "meeting"
    assert body["documents"][0]["created_at"] == ts_newer.isoformat()
    assert body["documents"][1]["document_id"] == "DOC_OLD"
    assert body["documents"][1]["pages"] == 84
    assert body["documents"][1]["created_at"] == ts_older.isoformat()

    # Verify query targets `uploaded_at`, not the legacy `created_at`
    # 쿼리가 `uploaded_at` 을 사용 (이전 버그였던 `created_at` 이 아님) 확인
    fetch_sql = str(mock_conn.fetch.call_args_list[0])
    assert "uploaded_at" in fetch_sql
    assert "created_at" not in fetch_sql
