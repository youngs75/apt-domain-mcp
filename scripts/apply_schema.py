"""Apply sql/schema.sql to DATABASE_URL.

Run from repo root:
    uv run python scripts/apply_schema.py
or in portal Web IDE:
    python scripts/apply_schema.py

Reads DATABASE_URL from environment. Loads .env if present (simple parser).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(repo_root / ".env")

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    schema_path = repo_root / "sql" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")

    print(f"Connecting to {url.split('@')[-1]} ...")
    conn = await asyncpg.connect(url)
    try:
        print(f"Applying {schema_path.relative_to(repo_root)} ({len(sql)} bytes) ...")
        await conn.execute(sql)
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        print("Tables in public schema:")
        for row in tables:
            print(f"  - {row['tablename']}")
    finally:
        await conn.close()

    print("Schema applied successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
