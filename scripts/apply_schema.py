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


def _split_sql(sql: str) -> list[str]:
    """Split SQL into statements, respecting dollar-quoted blocks ($$...$$)."""
    stmts: list[str] = []
    buf: list[str] = []
    in_dollar_quote = False
    i = 0
    while i < len(sql):
        if not in_dollar_quote and sql[i:i+2] == "$$":
            in_dollar_quote = True
            buf.append("$$")
            i += 2
        elif in_dollar_quote and sql[i:i+2] == "$$":
            in_dollar_quote = False
            buf.append("$$")
            i += 2
        elif not in_dollar_quote and sql[i] == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
        else:
            buf.append(sql[i])
            i += 1
    last = "".join(buf).strip()
    if last:
        stmts.append(last)
    return stmts


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
        # Execute statement by statement so pg_trgm-dependent lines can be skipped
        # when the extension is unavailable (shared RDS without superuser).
        # Split on ";" but respect dollar-quoted blocks ($$...$$).
        stmts = _split_sql(sql)
        skipped = 0
        for stmt in stmts:
            try:
                await conn.execute(stmt)
            except asyncpg.InsufficientPrivilegeError as e:
                print(f"  SKIP (no privilege): {stmt[:80].replace(chr(10),' ')} ...")
                skipped += 1
            except asyncpg.UndefinedObjectError as e:
                # gin_trgm_ops not available without pg_trgm
                print(f"  SKIP (undefined object): {stmt[:80].replace(chr(10),' ')} ...")
                skipped += 1
        if skipped:
            print(f"  ({skipped} statements skipped — pg_trgm not available)")
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
