"""Embedding utilities — generate vectors via OpenAI-compatible embedding API.

Supports any embedding model exposed via an OpenAI-compatible endpoint:
- OpenAI (text-embedding-3-small/large)
- AWS Bedrock via LiteLLM (amazon.titan-embed-text-v2)
- Ollama (nomic-embed-text)
- Any other OpenAI-compatible provider

Cost: ~$0.02 per 2000 chunks (one-time). $0.000002 per user query.
Incremental: Only embeds rows where embedding is NULL (skips already-embedded chunks).
"""

import os

import httpx

from nexus_ai.config import settings
from nexus_ai.db.postgres import get_pg_pool

# Configurable embedding model — override via EMBED_MODEL env var
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "1536"))


async def embed_text(text: str) -> list[float] | None:
    """Generate embedding vector for a single text string.

    Returns a float vector of EMBED_DIMENSIONS, or None on failure.
    """
    if not text or not text.strip():
        return None

    try:
        base_url = settings.litellm_endpoint.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url}/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.litellm_api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": EMBED_MODEL, "input": text[:8000]},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["data"][0]["embedding"]
    except Exception:
        pass
    return None


async def backfill_embeddings() -> dict:
    """Backfill embeddings for all chunks that don't have one yet.

    Only processes rows where embedding IS NULL — safe to re-run.
    Returns stats: {total, embedded, skipped, failed}
    """
    pool = await get_pg_pool()
    stats = {"total": 0, "embedded": 0, "skipped": 0, "failed": 0}

    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM embeddings")
        missing = await conn.fetchval("SELECT count(*) FROM embeddings WHERE embedding IS NULL")
        stats["total"] = total
        stats["skipped"] = total - missing

        if missing == 0:
            return stats

        rows = await conn.fetch(
            "SELECT id, content FROM embeddings WHERE embedding IS NULL ORDER BY id LIMIT 100"
        )

        while rows:
            for row in rows:
                vec = await embed_text(row["content"])
                if vec:
                    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                    await conn.execute(
                        "UPDATE embeddings SET embedding = $1::vector WHERE id = $2",
                        vec_str, row["id"],
                    )
                    stats["embedded"] += 1
                else:
                    stats["failed"] += 1

            last_id = rows[-1]["id"]
            rows = await conn.fetch(
                "SELECT id, content FROM embeddings WHERE embedding IS NULL AND id > $1 ORDER BY id LIMIT 100",
                last_id,
            )

    return stats
