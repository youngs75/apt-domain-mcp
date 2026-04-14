"""Shared asyncpg pool accessor for MCP tools."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str | None = None) -> asyncpg.Pool | None:
    """Create the shared pool if DATABASE_URL is set.

    Returns None if DATABASE_URL is not configured (server still boots with
    read-only placeholder responses for environments without Postgres)."""
    global _pool
    if _pool is not None:
        return _pool
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    pool = get_pool()
    if pool is None:
        raise RuntimeError("DB_NOT_CONFIGURED")
    async with pool.acquire() as conn:
        yield conn
