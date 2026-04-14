"""Generic ingest CLI.

    python -m apt_domain_mcp.ingest.cli --complex-id <id> --kind regulation --file <path>
    python -m apt_domain_mcp.ingest.cli --complex-id <id> --kind regulation-diff --file <path>
    python -m apt_domain_mcp.ingest.cli --complex-id <id> --kind meeting --file <path>

This is the single-file variant. For the full pilot dataset use:
    python -m apt_domain_mcp.ingest.seed
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
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
from .seed import load_dotenv
from .tagging import tag_article, tag_decision


async def _run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    path = Path(args.file).resolve()
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            if args.kind == "regulation":
                reg = parse_regulation_markdown(path)
                for art in reg.articles:
                    tag_article(art)
                doc = await upsert_document(
                    conn,
                    complex_id=args.complex_id,
                    kind="regulation",
                    title=f"관리규약 v{reg.version}",
                    source_path=str(path),
                    raw_text=path.read_text(encoding="utf-8"),
                )
                await ingest_regulation(
                    conn,
                    complex_id=args.complex_id,
                    regulation=reg,
                    source_document=doc,
                    make_current=args.make_current,
                )
                print(f"ingested regulation v{reg.version}: {len(reg.articles)} articles")

            elif args.kind == "regulation-diff":
                diff = parse_regulation_diff(path)
                await upsert_regulation_diff(
                    conn, complex_id=args.complex_id, diff=diff
                )
                print(
                    f"ingested revisions v{diff.from_version}→v{diff.to_version}: "
                    f"{len(diff.entries)} entries"
                )

            elif args.kind == "meeting":
                meeting = parse_meeting_markdown(path)
                for d in meeting.decisions:
                    tag_decision(d)
                doc = await upsert_document(
                    conn,
                    complex_id=args.complex_id,
                    kind="meeting",
                    title=f"회의록 {meeting.meeting_date} ({meeting.meeting_type})",
                    source_path=str(path),
                    raw_text=meeting.raw_text,
                )
                mid = await upsert_meeting(
                    conn,
                    complex_id=args.complex_id,
                    meeting=meeting,
                    source_document=doc,
                )
                print(
                    f"ingested meeting {meeting.meeting_date} "
                    f"({meeting.meeting_type}): {len(meeting.decisions)} decisions -> {mid}"
                )
            else:
                print(f"unknown --kind: {args.kind}", file=sys.stderr)
                return 2
    finally:
        await conn.close()
    return 0


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    load_dotenv(repo_root / ".env")

    p = argparse.ArgumentParser(prog="apt-domain-mcp.ingest")
    p.add_argument("--complex-id", required=True)
    p.add_argument("--kind", required=True, choices=["regulation", "regulation-diff", "meeting"])
    p.add_argument("--file", required=True)
    p.add_argument("--make-current", action="store_true", help="regulation일 때 현행 버전으로 지정")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
