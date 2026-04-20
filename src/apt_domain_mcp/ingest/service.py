"""High-level ingest service for admin REST API.

Wraps the same parser + repository logic used by the CLI, but designed for
HTTP callers (returns structured result instead of printing).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from .parser_meeting import parse_meeting_markdown
from .parser_regulation import parse_regulation_markdown
from .parser_regulation_diff import parse_regulation_diff
from .repository import (
    ingest_regulation,
    upsert_document,
    upsert_meeting,
    upsert_regulation_diff,
)
from .tagging import tag_article, tag_decision

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    kind: str
    success: bool
    message: str
    details: dict = field(default_factory=dict)


async def run_ingest(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    kind: str,
    file_path: Path,
    make_current: bool = False,
) -> IngestResult:
    raw_text = file_path.read_text(encoding="utf-8")

    if kind == "regulation":
        reg = parse_regulation_markdown(file_path)
        for art in reg.articles:
            tag_article(art)
        doc = await upsert_document(
            conn,
            complex_id=complex_id,
            kind="regulation",
            title=f"관리규약 v{reg.version}",
            source_path=str(file_path),
            raw_text=raw_text,
        )
        await ingest_regulation(
            conn,
            complex_id=complex_id,
            regulation=reg,
            source_document=doc,
            make_current=make_current,
        )
        return IngestResult(
            kind="regulation",
            success=True,
            message=f"Ingested regulation v{reg.version}: {len(reg.articles)} articles",
            details={"version": reg.version, "article_count": len(reg.articles)},
        )

    elif kind == "regulation-diff":
        diff = parse_regulation_diff(file_path)
        await upsert_regulation_diff(conn, complex_id=complex_id, diff=diff)
        return IngestResult(
            kind="regulation-diff",
            success=True,
            message=f"Ingested revisions v{diff.from_version}->v{diff.to_version}: {len(diff.entries)} entries",
            details={
                "from_version": diff.from_version,
                "to_version": diff.to_version,
                "entry_count": len(diff.entries),
            },
        )

    elif kind == "meeting":
        meeting = parse_meeting_markdown(file_path)
        for d in meeting.decisions:
            tag_decision(d)
        doc = await upsert_document(
            conn,
            complex_id=complex_id,
            kind="meeting",
            title=f"회의록 {meeting.meeting_date} ({meeting.meeting_type})",
            source_path=str(file_path),
            raw_text=meeting.raw_text,
        )
        mid = await upsert_meeting(
            conn,
            complex_id=complex_id,
            meeting=meeting,
            source_document=doc,
        )
        return IngestResult(
            kind="meeting",
            success=True,
            message=f"Ingested meeting {meeting.meeting_date}: {len(meeting.decisions)} decisions",
            details={
                "meeting_id": mid,
                "meeting_date": str(meeting.meeting_date),
                "decision_count": len(meeting.decisions),
            },
        )

    else:
        return IngestResult(
            kind=kind,
            success=False,
            message=f"Unknown kind: {kind}. Expected: regulation, regulation-diff, meeting",
        )
