"""CLI for the wiki generator.

Usage:
    python -m apt_domain_mcp.wiki.cli --complex-id X --topic 주차
    python -m apt_domain_mcp.wiki.cli --complex-id X --all
    python -m apt_domain_mcp.wiki.cli --complex-id X --all --force
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from . import generator as gen


async def _run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    load_dotenv(repo_root / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    print(f"Connecting to {url.split('@')[-1]} ...")
    conn = await asyncpg.connect(url)
    try:
        complex_row = await conn.fetchrow(
            "SELECT complex_id FROM complex WHERE complex_id = $1",
            args.complex_id,
        )
        if not complex_row:
            print(f"ERROR: complex not found: {args.complex_id}", file=sys.stderr)
            return 2

        if args.all:
            topics = args.topic or gen.DEFAULT_TOPICS
            results = await gen.generate_all_topics(
                conn,
                complex_id=args.complex_id,
                topics=tuple(topics) if isinstance(topics, list) else topics,
                force=args.force,
            )
            for r in results:
                print(_format_result(r))
            ok = sum(1 for r in results if r["status"] == "generated")
            skipped = sum(1 for r in results if r["status"].startswith("skipped"))
            failed = sum(1 for r in results if r["status"].startswith("failed"))
            print(f"\nSummary: generated={ok} skipped={skipped} failed={failed}")
        else:
            if not args.topic:
                print("ERROR: --topic 또는 --all 중 하나가 필요합니다.", file=sys.stderr)
                return 3
            topic = args.topic[0] if isinstance(args.topic, list) else args.topic
            r = await gen.generate_topic_page(
                conn, complex_id=args.complex_id, topic=topic, force=args.force
            )
            print(_format_result(r))
    finally:
        await conn.close()
    return 0


def _format_result(r: dict) -> str:
    status = r["status"]
    topic = r["topic"]
    if status == "generated":
        return (
            f"[OK] {topic}: generated "
            f"articles={r['article_count']} decisions={r['decision_count']} "
            f"body_chars={r['body_chars']}"
        )
    if status == "skipped_no_evidence":
        return f"[--] {topic}: skipped (no evidence)"
    if status == "skipped_unchanged":
        return f"[==] {topic}: skipped (unchanged hash)"
    if status == "failed_llm":
        return f"[!!] {topic}: LLM call failed"
    return f"[??] {topic}: {status}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate wiki pages via LLM")
    parser.add_argument("--complex-id", required=True)
    parser.add_argument(
        "--topic",
        action="append",
        help="토픽명 (여러 번 지정 가능). 생략 시 --all 필요",
    )
    parser.add_argument("--all", action="store_true", help="DEFAULT_TOPICS 전체 생성")
    parser.add_argument(
        "--force", action="store_true", help="source_hash 변동 여부와 무관하게 재생성"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
