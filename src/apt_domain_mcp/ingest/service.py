"""Shared ingest logic used by both CLI and admin API.

Extracts the core ingest pipeline from cli.py so it can be called with
either a standalone connection (CLI) or a pooled connection (API).
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
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


@dataclass
class IngestResult:
    kind: str
    message: str
    count: int


async def run_ingest(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    kind: str,
    file_content: str,
    filename: str,
    make_current: bool = False,
) -> IngestResult:
    """Run the full ingest pipeline inside an existing connection.

    The caller is responsible for wrapping this in a transaction if desired.
    """
    # Parsers expect a Path; write content to a temp file.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    )
    tmp.write(file_content)
    tmp.close()
    tmp_path = Path(tmp.name)
    try:
        return await _do_ingest(
            conn,
            complex_id=complex_id,
            kind=kind,
            tmp_path=tmp_path,
            file_content=file_content,
            filename=filename,
            make_current=make_current,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


async def _do_ingest(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    kind: str,
    tmp_path: Path,
    file_content: str,
    filename: str,
    make_current: bool,
) -> IngestResult:
    if kind == "regulation":
        reg = parse_regulation_markdown(tmp_path)
        for art in reg.articles:
            tag_article(art)
        doc = await upsert_document(
            conn,
            complex_id=complex_id,
            kind="regulation",
            title=f"관리규약 v{reg.version}",
            source_path=filename,
            raw_text=file_content,
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
            message=f"관리규약 v{reg.version} 인제스트 완료",
            count=len(reg.articles),
        )

    elif kind == "regulation-diff":
        diff = parse_regulation_diff(tmp_path)
        await upsert_regulation_diff(conn, complex_id=complex_id, diff=diff)
        return IngestResult(
            kind="regulation-diff",
            message=f"개정 v{diff.from_version}→v{diff.to_version} 인제스트 완료",
            count=len(diff.entries),
        )

    elif kind == "meeting":
        meeting = parse_meeting_markdown(tmp_path)
        for d in meeting.decisions:
            tag_decision(d)
        doc = await upsert_document(
            conn,
            complex_id=complex_id,
            kind="meeting",
            title=f"회의록 {meeting.meeting_date} ({meeting.meeting_type})",
            source_path=filename,
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
            message=f"회의록 {meeting.meeting_date} 인제스트 완료 (id={mid})",
            count=len(meeting.decisions),
        )

    else:
        raise ValueError(f"Unknown kind: {kind}")
