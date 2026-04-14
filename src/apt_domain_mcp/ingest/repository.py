"""asyncpg-based upsert functions for ingest.

Kept intentionally small: no ORM, raw SQL. All functions take an asyncpg
connection (or transaction) so callers can batch everything in one tx.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import date

import asyncpg

from .models import (
    ParsedArticle,
    ParsedDecision,
    ParsedMeeting,
    ParsedRegulation,
    ParsedRegulationDiff,
)


def new_ulid_like() -> str:
    """Cheap ULID-ish identifier (26 chars, time-sortable prefix).

    Real ULID library not pulled in to keep deps light; lexicographic sort
    still works because the prefix is hex of time."""
    import time

    ts = int(time.time() * 1000)
    rnd = secrets.token_hex(8)
    return f"{ts:013x}{rnd}".upper()[:26]


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Complex
# --------------------------------------------------------------------------
async def upsert_complex(conn: asyncpg.Connection, complex_info: dict) -> None:
    await conn.execute(
        """
        INSERT INTO complex (
            complex_id, name, address, sido, sigungu, units, buildings,
            max_floors, use_approval_date, management_type, heating_type,
            parking_slots, external_ids
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb
        )
        ON CONFLICT (complex_id) DO UPDATE SET
            name = EXCLUDED.name,
            address = EXCLUDED.address,
            sido = EXCLUDED.sido,
            sigungu = EXCLUDED.sigungu,
            units = EXCLUDED.units,
            buildings = EXCLUDED.buildings,
            max_floors = EXCLUDED.max_floors,
            use_approval_date = EXCLUDED.use_approval_date,
            management_type = EXCLUDED.management_type,
            heating_type = EXCLUDED.heating_type,
            parking_slots = EXCLUDED.parking_slots,
            external_ids = EXCLUDED.external_ids,
            updated_at = now()
        """,
        complex_info["complex_id"],
        complex_info["name"],
        complex_info["address"],
        complex_info.get("sido"),
        complex_info.get("sigungu"),
        complex_info.get("units"),
        complex_info.get("buildings"),
        complex_info.get("max_floors"),
        date.fromisoformat(complex_info["use_approval_date"])
        if complex_info.get("use_approval_date")
        else None,
        complex_info.get("management_type"),
        complex_info.get("heating_type"),
        complex_info.get("parking_slots"),
        __import__("json").dumps(complex_info.get("external_ids") or {}, ensure_ascii=False),
    )


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------
async def upsert_document(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    kind: str,
    title: str,
    source_path: str,
    raw_text: str,
    pages: int | None = None,
) -> str:
    sha = sha256_of(raw_text)
    row = await conn.fetchrow(
        """
        SELECT document_id FROM document
        WHERE complex_id = $1 AND sha256 = $2
        """,
        complex_id,
        sha,
    )
    if row:
        return row["document_id"]
    doc_id = new_ulid_like()
    await conn.execute(
        """
        INSERT INTO document (
            document_id, complex_id, kind, title, source_path, sha256, pages
        ) VALUES ($1,$2,$3,$4,$5,$6,$7)
        """,
        doc_id,
        complex_id,
        kind,
        title,
        source_path,
        sha,
        pages,
    )
    return doc_id


# --------------------------------------------------------------------------
# Regulation
# --------------------------------------------------------------------------
async def upsert_regulation_version(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    version: int,
    effective_date: date,
    source_document: str | None,
    summary: str | None,
    make_current: bool,
) -> None:
    # make_current은 트리거가 아니라 호출자가 관리
    if make_current:
        await conn.execute(
            "UPDATE regulation_version SET is_current = false WHERE complex_id = $1",
            complex_id,
        )
    await conn.execute(
        """
        INSERT INTO regulation_version (
            complex_id, version, effective_date, source_document, summary, is_current
        ) VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (complex_id, version) DO UPDATE SET
            effective_date = EXCLUDED.effective_date,
            source_document = EXCLUDED.source_document,
            summary = EXCLUDED.summary,
            is_current = EXCLUDED.is_current
        """,
        complex_id,
        version,
        effective_date,
        source_document,
        summary,
        make_current,
    )


async def upsert_regulation_articles(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    version: int,
    articles: list[ParsedArticle],
) -> None:
    # 전량 교체 (해당 version 내에서는 진실의 원천이 파일)
    await conn.execute(
        "DELETE FROM regulation_article WHERE complex_id = $1 AND version = $2",
        complex_id,
        version,
    )
    for art in articles:
        await conn.execute(
            """
            INSERT INTO regulation_article (
                complex_id, version, article_number, article_seq,
                chapter_number, chapter_title, title, body,
                category, tags, referenced_articles, referenced_laws
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12
            )
            """,
            complex_id,
            version,
            art.article_number,
            art.article_seq,
            art.chapter_number,
            art.chapter_title,
            art.title,
            art.body,
            art.category,
            art.tags,
            art.referenced_articles,
            art.referenced_laws,
        )


async def upsert_regulation_diff(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    diff: ParsedRegulationDiff,
) -> None:
    await conn.execute(
        """
        DELETE FROM regulation_revision
        WHERE complex_id = $1 AND from_version = $2 AND to_version = $3
        """,
        complex_id,
        diff.from_version,
        diff.to_version,
    )
    for e in diff.entries:
        await conn.execute(
            """
            INSERT INTO regulation_revision (
                complex_id, from_version, to_version, article_number,
                change_type, old_body, new_body, reason
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            complex_id,
            diff.from_version,
            diff.to_version,
            e.article_number,
            e.change_type,
            e.old_body,
            e.new_body,
            e.reason,
        )


# --------------------------------------------------------------------------
# Meeting
# --------------------------------------------------------------------------
async def upsert_meeting(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    meeting: ParsedMeeting,
    source_document: str | None,
) -> str:
    row = await conn.fetchrow(
        """
        SELECT meeting_id FROM meeting
        WHERE complex_id = $1 AND meeting_date = $2 AND meeting_type = $3
        """,
        complex_id,
        meeting.meeting_date,
        meeting.meeting_type,
    )
    if row:
        meeting_id = row["meeting_id"]
        await conn.execute(
            """
            UPDATE meeting SET
                attendees_count = $2,
                quorum = $3,
                source_document = $4,
                raw_text = $5
            WHERE meeting_id = $1
            """,
            meeting_id,
            meeting.attendees_count,
            meeting.quorum,
            source_document,
            meeting.raw_text,
        )
    else:
        meeting_id = new_ulid_like()
        await conn.execute(
            """
            INSERT INTO meeting (
                meeting_id, complex_id, meeting_date, meeting_type,
                attendees_count, quorum, source_document, raw_text
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            meeting_id,
            complex_id,
            meeting.meeting_date,
            meeting.meeting_type,
            meeting.attendees_count,
            meeting.quorum,
            source_document,
            meeting.raw_text,
        )

    # replace decisions
    await conn.execute("DELETE FROM meeting_decision WHERE meeting_id = $1", meeting_id)
    for d in meeting.decisions:
        await _insert_decision(conn, meeting_id=meeting_id, complex_id=complex_id, decision=d)
    return meeting_id


async def _insert_decision(
    conn: asyncpg.Connection,
    *,
    meeting_id: str,
    complex_id: str,
    decision: ParsedDecision,
) -> None:
    await conn.execute(
        """
        INSERT INTO meeting_decision (
            decision_id, meeting_id, complex_id, agenda_seq, topic, category,
            decision, result, vote_for, vote_against, vote_abstain,
            related_articles, follow_up
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        """,
        new_ulid_like(),
        meeting_id,
        complex_id,
        decision.agenda_seq,
        decision.topic,
        decision.category,
        decision.decision,
        decision.result,
        decision.vote_for,
        decision.vote_against,
        decision.vote_abstain,
        decision.related_articles,
        decision.follow_up,
    )


# --------------------------------------------------------------------------
# Regulation composite
# --------------------------------------------------------------------------
async def ingest_regulation(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    regulation: ParsedRegulation,
    source_document: str | None,
    make_current: bool,
) -> None:
    await upsert_regulation_version(
        conn,
        complex_id=complex_id,
        version=regulation.version,
        effective_date=regulation.effective_date,
        source_document=source_document,
        summary=regulation.summary,
        make_current=make_current,
    )
    await upsert_regulation_articles(
        conn,
        complex_id=complex_id,
        version=regulation.version,
        articles=regulation.articles,
    )
