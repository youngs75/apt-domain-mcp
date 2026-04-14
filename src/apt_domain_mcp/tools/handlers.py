"""Implementation of the 8 MCP tools (2 admin + 6 query).

Contract:
- All tools return a JSON-serializable dict (the server wraps with
  `json.dumps(ensure_ascii=False, indent=2)`).
- Errors return {"error": CODE, "message": str}. Codes:
    DB_NOT_CONFIGURED       — DATABASE_URL not set (deploy-time issue)
    INVALID_PARAMS          — missing/blank complex_id (or other)
    COMPLEX_NOT_FOUND       — complex_id is not in the complex table
    ARTICLE_NOT_FOUND
    MEETING_NOT_FOUND
    WIKI_NOT_FOUND
"""
from __future__ import annotations

from typing import Any

from .. import db


# --------------------------------------------------------------------------
def _err(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def _require_complex_id(complex_id: str | None) -> dict[str, Any] | None:
    if not complex_id or not complex_id.strip():
        return _err("INVALID_PARAMS", "complex_id는 필수입니다.")
    return None


async def _ensure_complex(conn, complex_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT 1 FROM complex WHERE complex_id = $1", complex_id
    )
    if not row:
        return _err(
            "COMPLEX_NOT_FOUND",
            f"등록되지 않은 단지입니다: {complex_id}. list_complexes로 확인하세요.",
        )
    return None


# --------------------------------------------------------------------------
# list_complexes
# --------------------------------------------------------------------------
async def list_complexes() -> dict[str, Any]:
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")
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
    return {"count": len(complexes), "complexes": complexes}


def _json_loads(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    import json as _json
    try:
        return _json.loads(v)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# get_complex_info
# --------------------------------------------------------------------------
async def get_complex_info(complex_id: str) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM complex WHERE complex_id = $1
            """,
            complex_id,
        )
        if not row:
            return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지입니다: {complex_id}")

        reg_row = await conn.fetchrow(
            """
            SELECT version, effective_date FROM regulation_version
            WHERE complex_id = $1 AND is_current = true
            """,
            complex_id,
        )
        meeting_count = await conn.fetchval(
            "SELECT count(*) FROM meeting WHERE complex_id = $1", complex_id
        )

    return {
        "complex_id": row["complex_id"],
        "name": row["name"],
        "address": row["address"],
        "sido": row["sido"],
        "sigungu": row["sigungu"],
        "units": row["units"],
        "buildings": row["buildings"],
        "max_floors": row["max_floors"],
        "use_approval_date": row["use_approval_date"].isoformat() if row["use_approval_date"] else None,
        "management_type": row["management_type"],
        "heating_type": row["heating_type"],
        "parking_slots": row["parking_slots"],
        "external_ids": _json_loads(row["external_ids"]),
        "current_regulation": (
            {
                "version": reg_row["version"],
                "effective_date": reg_row["effective_date"].isoformat(),
            }
            if reg_row
            else None
        ),
        "meeting_count": int(meeting_count or 0),
    }


# --------------------------------------------------------------------------
# search_regulation
# --------------------------------------------------------------------------
async def search_regulation(
    complex_id: str,
    query: str,
    *,
    category: str | None = None,
    version: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if not query or not query.strip():
        return _err("INVALID_PARAMS", "query는 필수입니다.")
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")
    limit = max(1, min(int(limit or 10), 50))

    async with db.acquire() as conn:
        if (e := await _ensure_complex(conn, complex_id)):
            return e

        if version is None:
            ver_row = await conn.fetchrow(
                """
                SELECT version FROM regulation_version
                WHERE complex_id = $1 AND is_current = true
                """,
                complex_id,
            )
            if not ver_row:
                return _err("ARTICLE_NOT_FOUND", "현행 관리규약 버전이 없습니다.")
            version = ver_row["version"]

        sql = """
            SELECT article_number, article_seq, chapter_number, chapter_title,
                   title, body, category, referenced_articles, referenced_laws
            FROM regulation_article
            WHERE complex_id = $1 AND version = $2
              AND (body ILIKE '%' || $3 || '%' OR title ILIKE '%' || $3 || '%')
        """
        params: list = [complex_id, version, query]
        if category:
            sql += " AND $4 = ANY(category)"
            params.append(category)
        sql += " ORDER BY article_seq LIMIT " + str(limit)
        rows = await conn.fetch(sql, *params)

    return {
        "complex_id": complex_id,
        "version": version,
        "query": query,
        "category_filter": category,
        "count": len(rows),
        "results": [
            {
                "article_number": r["article_number"],
                "chapter": (
                    f"제{r['chapter_number']}장 {r['chapter_title']}"
                    if r["chapter_number"]
                    else None
                ),
                "title": r["title"],
                "body": r["body"],
                "category": list(r["category"] or []),
                "referenced_articles": list(r["referenced_articles"] or []),
                "referenced_laws": list(r["referenced_laws"] or []),
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# get_regulation_article
# --------------------------------------------------------------------------
async def get_regulation_article(
    complex_id: str,
    article_number: str,
    *,
    version: int | None = None,
    include_history: bool = True,
) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if not article_number:
        return _err("INVALID_PARAMS", "article_number는 필수입니다 (예: '제41조').")
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")

    async with db.acquire() as conn:
        if (e := await _ensure_complex(conn, complex_id)):
            return e

        if version is None:
            ver_row = await conn.fetchrow(
                "SELECT version FROM regulation_version WHERE complex_id = $1 AND is_current = true",
                complex_id,
            )
            if not ver_row:
                return _err("ARTICLE_NOT_FOUND", "현행 관리규약 버전이 없습니다.")
            version = ver_row["version"]

        row = await conn.fetchrow(
            """
            SELECT * FROM regulation_article
            WHERE complex_id = $1 AND version = $2 AND article_number = $3
            """,
            complex_id,
            version,
            article_number,
        )
        if not row:
            return _err(
                "ARTICLE_NOT_FOUND",
                f"v{version}에 {article_number}가 없습니다.",
            )

        history = []
        if include_history:
            rev_rows = await conn.fetch(
                """
                SELECT rr.from_version, rr.to_version, rr.change_type,
                       rr.old_body, rr.new_body, rr.reason,
                       rv.effective_date
                FROM regulation_revision rr
                JOIN regulation_version rv
                    ON rv.complex_id = rr.complex_id AND rv.version = rr.to_version
                WHERE rr.complex_id = $1 AND rr.article_number = $2
                ORDER BY rr.to_version
                """,
                complex_id,
                article_number,
            )
            history = [
                {
                    "from_version": r["from_version"],
                    "to_version": r["to_version"],
                    "effective_date": r["effective_date"].isoformat(),
                    "change_type": r["change_type"],
                    "old_body": r["old_body"],
                    "new_body": r["new_body"],
                    "reason": r["reason"],
                }
                for r in rev_rows
            ]

    return {
        "complex_id": complex_id,
        "version": version,
        "article_number": row["article_number"],
        "chapter": (
            f"제{row['chapter_number']}장 {row['chapter_title']}"
            if row["chapter_number"]
            else None
        ),
        "title": row["title"],
        "body": row["body"],
        "category": list(row["category"] or []),
        "referenced_articles": list(row["referenced_articles"] or []),
        "referenced_laws": list(row["referenced_laws"] or []),
        "revision_history": history,
    }


# --------------------------------------------------------------------------
# list_regulation_revisions
# --------------------------------------------------------------------------
async def list_regulation_revisions(complex_id: str) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")

    async with db.acquire() as conn:
        if (e := await _ensure_complex(conn, complex_id)):
            return e
        versions = await conn.fetch(
            """
            SELECT version, effective_date, summary, is_current
            FROM regulation_version
            WHERE complex_id = $1
            ORDER BY version
            """,
            complex_id,
        )
        revisions = await conn.fetch(
            """
            SELECT from_version, to_version, article_number, change_type, reason
            FROM regulation_revision
            WHERE complex_id = $1
            ORDER BY to_version, article_number
            """,
            complex_id,
        )

    return {
        "complex_id": complex_id,
        "versions": [
            {
                "version": r["version"],
                "effective_date": r["effective_date"].isoformat(),
                "summary": r["summary"],
                "is_current": r["is_current"],
            }
            for r in versions
        ],
        "revisions": [
            {
                "from_version": r["from_version"],
                "to_version": r["to_version"],
                "article_number": r["article_number"],
                "change_type": r["change_type"],
                "reason": r["reason"],
            }
            for r in revisions
        ],
    }


# --------------------------------------------------------------------------
# search_meeting_decisions
# --------------------------------------------------------------------------
async def search_meeting_decisions(
    complex_id: str,
    query: str | None = None,
    *,
    category: str | None = None,
    result: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")
    limit = max(1, min(int(limit or 20), 100))

    async with db.acquire() as conn:
        if (e := await _ensure_complex(conn, complex_id)):
            return e

        sql = [
            """
            SELECT md.decision_id, md.meeting_id, md.agenda_seq, md.topic,
                   md.category, md.decision, md.result,
                   md.vote_for, md.vote_against, md.vote_abstain,
                   md.related_articles, md.follow_up,
                   m.meeting_date, m.meeting_type
            FROM meeting_decision md
            JOIN meeting m ON m.meeting_id = md.meeting_id
            WHERE md.complex_id = $1
            """
        ]
        params: list = [complex_id]
        if query and query.strip():
            params.append(query)
            sql.append(f" AND (md.topic ILIKE '%' || ${len(params)} || '%' "
                       f"OR md.decision ILIKE '%' || ${len(params)} || '%' "
                       f"OR md.follow_up ILIKE '%' || ${len(params)} || '%')")
        if category:
            params.append(category)
            sql.append(f" AND ${len(params)} = ANY(md.category)")
        if result:
            params.append(result)
            sql.append(f" AND md.result = ${len(params)}")
        if date_from:
            params.append(_parse_date(date_from))
            sql.append(f" AND m.meeting_date >= ${len(params)}")
        if date_to:
            params.append(_parse_date(date_to))
            sql.append(f" AND m.meeting_date <= ${len(params)}")
        sql.append(" ORDER BY m.meeting_date DESC, md.agenda_seq ASC")
        sql.append(f" LIMIT {limit}")

        rows = await conn.fetch("".join(sql), *params)

    return {
        "complex_id": complex_id,
        "query": query,
        "filters": {
            "category": category,
            "result": result,
            "date_from": date_from,
            "date_to": date_to,
        },
        "count": len(rows),
        "results": [
            {
                "decision_id": r["decision_id"],
                "meeting_id": r["meeting_id"],
                "meeting_date": r["meeting_date"].isoformat(),
                "meeting_type": r["meeting_type"],
                "agenda_seq": r["agenda_seq"],
                "topic": r["topic"],
                "category": list(r["category"] or []),
                "decision": r["decision"],
                "result": r["result"],
                "vote": {
                    "for": r["vote_for"],
                    "against": r["vote_against"],
                    "abstain": r["vote_abstain"],
                }
                if r["vote_for"] is not None
                else None,
                "related_articles": list(r["related_articles"] or []),
                "follow_up": r["follow_up"],
            }
            for r in rows
        ],
    }


def _parse_date(s: str):
    from datetime import date as _d

    return _d.fromisoformat(s)


# --------------------------------------------------------------------------
# get_meeting_detail
# --------------------------------------------------------------------------
async def get_meeting_detail(complex_id: str, meeting_id: str) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if not meeting_id:
        return _err("INVALID_PARAMS", "meeting_id는 필수입니다.")
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")

    async with db.acquire() as conn:
        if (e := await _ensure_complex(conn, complex_id)):
            return e
        mrow = await conn.fetchrow(
            """
            SELECT * FROM meeting
            WHERE meeting_id = $1 AND complex_id = $2
            """,
            meeting_id,
            complex_id,
        )
        if not mrow:
            return _err("MEETING_NOT_FOUND", f"회의록을 찾을 수 없습니다: {meeting_id}")
        drows = await conn.fetch(
            """
            SELECT agenda_seq, topic, category, decision, result,
                   vote_for, vote_against, vote_abstain,
                   related_articles, follow_up
            FROM meeting_decision
            WHERE meeting_id = $1
            ORDER BY agenda_seq
            """,
            meeting_id,
        )

    return {
        "complex_id": complex_id,
        "meeting_id": meeting_id,
        "meeting_date": mrow["meeting_date"].isoformat(),
        "meeting_type": mrow["meeting_type"],
        "attendees_count": mrow["attendees_count"],
        "quorum": mrow["quorum"],
        "decisions": [
            {
                "agenda_seq": r["agenda_seq"],
                "topic": r["topic"],
                "category": list(r["category"] or []),
                "decision": r["decision"],
                "result": r["result"],
                "vote": {
                    "for": r["vote_for"],
                    "against": r["vote_against"],
                    "abstain": r["vote_abstain"],
                }
                if r["vote_for"] is not None
                else None,
                "related_articles": list(r["related_articles"] or []),
                "follow_up": r["follow_up"],
            }
            for r in drows
        ],
        "raw_text": mrow["raw_text"],
    }


# --------------------------------------------------------------------------
# get_wiki_page (Phase 1 후반 생성기 완성 전에는 빈 상태)
# --------------------------------------------------------------------------
async def get_wiki_page(complex_id: str, topic: str) -> dict[str, Any]:
    if (e := _require_complex_id(complex_id)):
        return e
    if not topic:
        return _err("INVALID_PARAMS", "topic은 필수입니다 (예: '주차', '반려동물').")
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "서버에 DATABASE_URL이 설정되지 않았습니다.")

    async with db.acquire() as conn:
        if (e := await _ensure_complex(conn, complex_id)):
            return e
        row = await conn.fetchrow(
            """
            SELECT title, body_md, source_refs, generator_model, last_generated_at
            FROM wiki_page
            WHERE complex_id = $1 AND topic = $2
            """,
            complex_id,
            topic,
        )
        if not row:
            return _err(
                "WIKI_NOT_FOUND",
                f"위키 페이지가 아직 생성되지 않았습니다: {topic}. "
                "Phase 1 후반 LLM Wiki 생성기 도입 이후 제공됩니다.",
            )
    return {
        "complex_id": complex_id,
        "topic": topic,
        "title": row["title"],
        "body_md": row["body_md"],
        "source_refs": _json_loads(row["source_refs"]),
        "generator_model": row["generator_model"],
        "last_generated_at": row["last_generated_at"].isoformat(),
    }
