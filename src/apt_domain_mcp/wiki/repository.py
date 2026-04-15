"""Wiki page persistence layer."""
from __future__ import annotations

import json

import asyncpg


async def upsert_wiki_page(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    topic: str,
    title: str,
    body_md: str,
    source_refs: list[dict],
    source_hash: str,
    generator_model: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO wiki_page (
            complex_id, topic, title, body_md, source_refs,
            source_hash, generator_model, last_generated_at
        ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7, now())
        ON CONFLICT (complex_id, topic) DO UPDATE SET
            title             = EXCLUDED.title,
            body_md           = EXCLUDED.body_md,
            source_refs       = EXCLUDED.source_refs,
            source_hash       = EXCLUDED.source_hash,
            generator_model   = EXCLUDED.generator_model,
            last_generated_at = now()
        """,
        complex_id,
        topic,
        title,
        body_md,
        json.dumps(source_refs, ensure_ascii=False),
        source_hash,
        generator_model,
    )


async def get_wiki_source_hash(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    topic: str,
) -> str | None:
    row = await conn.fetchrow(
        "SELECT source_hash FROM wiki_page WHERE complex_id = $1 AND topic = $2",
        complex_id,
        topic,
    )
    return row["source_hash"] if row else None
