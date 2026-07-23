"""PostgreSQL + pgvector connection management."""

import asyncpg

from nexus_ai.config import settings

_pool: asyncpg.Pool | None = None


async def get_pg_pool() -> asyncpg.Pool:
    """Get or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_size=2,
            max_size=10,
        )
    return _pool


async def check_pg_connection() -> bool:
    """Verify PostgreSQL connectivity and pgvector extension. Returns True if healthy."""
    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            # Check basic connectivity
            result = await conn.fetchval("SELECT 1")
            if result != 1:
                return False
            # Check pgvector extension is available
            ext = await conn.fetchval(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            )
            return ext == "vector"
    except Exception:
        return False


async def close_pg():
    """Close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
