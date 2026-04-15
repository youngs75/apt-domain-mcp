"""Seed the synthetic complex end-to-end.

    python -m apt_domain_mcp.ingest.seed

Reads DATABASE_URL from environment (.env auto-loaded). Idempotent: running
twice produces the same state — existing rows are updated in place.

Order:
  1. complex (한빛마을 새솔아파트)
  2. regulation v1 (full text) → articles
  3. regulation v2 diff → v2 articles (v1 + diff) + revision rows
  4. regulation v3 diff → v3 articles (v2 + diff) + revision rows (v3 becomes current)
  5. meetings (3 files) with decisions
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

from .parser_meeting import parse_meeting_markdown
from .parser_regulation import parse_regulation_markdown
from .parser_regulation_diff import apply_diff_to_articles, parse_regulation_diff
from .repository import (
    ingest_regulation,
    upsert_complex,
    upsert_document,
    upsert_meeting,
    upsert_regulation_diff,
)
from .tagging import get_llm_stats, reset_llm_stats, tag_article, tag_decision

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
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def seed(conn: asyncpg.Connection, repo_root: Path) -> None:
    syn_dir = repo_root / "synthetic"

    # 1. complex -------------------------------------------------------------
    await upsert_complex(conn, SYNTHETIC_COMPLEX)
    print(f"  complex: {SYNTHETIC_COMPLEX['name']} ({SYNTHETIC_COMPLEX_ID})")

    # 2. regulation v1 -------------------------------------------------------
    v1_path = syn_dir / "regulation_v1.md"
    v1 = parse_regulation_markdown(v1_path, version=1)
    for art in v1.articles:
        tag_article(art)
    v1_doc = await upsert_document(
        conn,
        complex_id=SYNTHETIC_COMPLEX_ID,
        kind="regulation",
        title="관리규약 v1 (제정)",
        source_path=str(v1_path.relative_to(repo_root)).replace("\\", "/"),
        raw_text=v1_path.read_text(encoding="utf-8"),
    )
    await ingest_regulation(
        conn,
        complex_id=SYNTHETIC_COMPLEX_ID,
        regulation=v1,
        source_document=v1_doc,
        make_current=False,
    )
    print(f"  regulation v1: {len(v1.articles)} articles ({v1.effective_date})")

    # 3. regulation v2 diff --------------------------------------------------
    v2_diff_path = syn_dir / "regulation_v2_diff.md"
    v2_diff = parse_regulation_diff(v2_diff_path)
    v2_articles = apply_diff_to_articles(v1.articles, v2_diff)
    for art in v2_articles:
        tag_article(art)
    from .models import ParsedRegulation  # local import

    v2 = ParsedRegulation(
        version=v2_diff.to_version,
        effective_date=v2_diff.effective_date,
        summary="v1→v2 개정 (관리비 부과기준, 주차장, 반려동물 신설)",
        articles=v2_articles,
    )
    v2_doc = await upsert_document(
        conn,
        complex_id=SYNTHETIC_COMPLEX_ID,
        kind="regulation",
        title="관리규약 v2 개정본",
        source_path=str(v2_diff_path.relative_to(repo_root)).replace("\\", "/"),
        raw_text=v2_diff_path.read_text(encoding="utf-8"),
    )
    await ingest_regulation(
        conn,
        complex_id=SYNTHETIC_COMPLEX_ID,
        regulation=v2,
        source_document=v2_doc,
        make_current=False,
    )
    await upsert_regulation_diff(
        conn, complex_id=SYNTHETIC_COMPLEX_ID, diff=v2_diff
    )
    print(
        f"  regulation v2: {len(v2_articles)} articles / "
        f"{len(v2_diff.entries)} revisions"
    )

    # 4. regulation v3 diff --------------------------------------------------
    v3_diff_path = syn_dir / "regulation_v3_diff.md"
    v3_diff = parse_regulation_diff(v3_diff_path)
    v3_articles = apply_diff_to_articles(v2_articles, v3_diff)
    for art in v3_articles:
        tag_article(art)
    v3 = ParsedRegulation(
        version=v3_diff.to_version,
        effective_date=v3_diff.effective_date,
        summary="v2→v3 개정 (대표회의 정원, 장충금 요율, 층간소음 신설)",
        articles=v3_articles,
    )
    v3_doc = await upsert_document(
        conn,
        complex_id=SYNTHETIC_COMPLEX_ID,
        kind="regulation",
        title="관리규약 v3 개정본 (현행)",
        source_path=str(v3_diff_path.relative_to(repo_root)).replace("\\", "/"),
        raw_text=v3_diff_path.read_text(encoding="utf-8"),
    )
    await ingest_regulation(
        conn,
        complex_id=SYNTHETIC_COMPLEX_ID,
        regulation=v3,
        source_document=v3_doc,
        make_current=True,
    )
    await upsert_regulation_diff(
        conn, complex_id=SYNTHETIC_COMPLEX_ID, diff=v3_diff
    )
    print(
        f"  regulation v3: {len(v3_articles)} articles / "
        f"{len(v3_diff.entries)} revisions (current)"
    )

    # 5. meetings ------------------------------------------------------------
    meetings_dir = syn_dir / "meetings"
    if meetings_dir.exists():
        for md_path in sorted(meetings_dir.glob("*.md")):
            meeting = parse_meeting_markdown(md_path)
            for d in meeting.decisions:
                tag_decision(d)
            m_doc = await upsert_document(
                conn,
                complex_id=SYNTHETIC_COMPLEX_ID,
                kind="meeting",
                title=f"회의록 {meeting.meeting_date.isoformat()} ({meeting.meeting_type})",
                source_path=str(md_path.relative_to(repo_root)).replace("\\", "/"),
                raw_text=meeting.raw_text,
            )
            mid = await upsert_meeting(
                conn,
                complex_id=SYNTHETIC_COMPLEX_ID,
                meeting=meeting,
                source_document=m_doc,
            )
            print(
                f"  meeting {meeting.meeting_date} {meeting.meeting_type}: "
                f"{len(meeting.decisions)} decisions -> {mid}"
            )


async def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    load_dotenv(repo_root / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    from . import tagging as _tagging_mod
    print(f"Loaded tagging module from: {_tagging_mod.__file__}")
    print(f"Connecting to {url.split('@')[-1]} ...")
    reset_llm_stats()
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            print("Seeding synthetic complex:")
            await seed(conn, repo_root)
    finally:
        await conn.close()
    ok, fail = get_llm_stats()
    print(f"Tagger stats: llm_ok={ok} llm_fail={fail} (fail = keyword fallback)")
    print("Seed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
