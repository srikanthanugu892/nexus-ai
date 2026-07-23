"""Search tools — hybrid semantic + text search over pgvector embeddings."""

import json
import re

from nexus_ai.db.postgres import get_pg_pool

# Keywords that signal the user wants API/endpoint info (boost postman/swagger sources)
_API_SIGNAL_WORDS = {"endpoint", "api", "post", "get", "put", "delete", "url", "request", "response", "body", "payload", "route", "path", "endpoints"}


def _detect_source_boost(query: str) -> str | None:
    """Detect if query is asking about APIs/endpoints and return source_type to boost."""
    q_lower = query.lower()
    words = set(re.findall(r'\w+', q_lower))
    api_matches = words & _API_SIGNAL_WORDS
    if len(api_matches) >= 1:
        return "api"
    return None


def _reorder_with_boost(results: list[dict], boost_type: str | None) -> list[dict]:
    """Re-order results to prioritize relevant source types."""
    if not boost_type:
        return results

    if boost_type == "api":
        priority = {"postman": 0, "swagger": 1, "confluence": 2, "db_schema": 3}
    else:
        return results

    return sorted(results, key=lambda r: priority.get(r.get("source_type", ""), 99))


async def search_documentation(query: str) -> str:
    """Hybrid search: vector similarity (semantic) + text fallback.

    Search strategy:
    1. If embeddings exist: embed query → cosine similarity search
    2. Fallback to text ILIKE if no embeddings or vector search returns nothing
    3. Apply source-type boosting for API-related queries

    Searches across: Swagger specs, Postman collections, Confluence docs, DB schemas.
    """
    from nexus_ai.tools.embeddings import embed_text

    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM embeddings")

        if count == 0:
            return json.dumps({
                "results": [],
                "note": "No documentation has been indexed yet. Run collectors to populate the search index.",
            })

        boost_type = _detect_source_boost(query)
        rows = []
        search_type = "text_fallback"

        # Vector search (primary)
        has_embeddings = await conn.fetchval("SELECT count(*) FROM embeddings WHERE embedding IS NOT NULL")
        if has_embeddings > 0:
            query_vec = await embed_text(query)
            if query_vec:
                vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
                rows = await conn.fetch(
                    """
                    SELECT content, source_type, source_url, service_name, metadata,
                           embedding <=> $1::vector AS distance
                    FROM embeddings
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT 15
                    """,
                    vec_str,
                )
                search_type = "vector"

        # Text fallback
        if not rows:
            rows = await _text_search(conn, query)
            search_type = "text_fallback"

        # Build results
        results = []
        for row in rows[:10]:
            results.append({
                "content": row["content"][:500],
                "source_type": row["source_type"],
                "source_url": row["source_url"],
                "service_name": row["service_name"],
            })

        results = _reorder_with_boost(results, boost_type)
        results = results[:10]

        if not results:
            return json.dumps({
                "results": [],
                "note": f"No documentation found matching '{query}'.",
            })

        return json.dumps({
            "results": results,
            "count": len(results),
            "search_type": search_type,
            "boost_applied": boost_type,
        })


async def _text_search(conn, query: str) -> list:
    """Multi-stage text search: exact phrase → AND keywords → OR ranked."""

    # Exact phrase
    rows = await conn.fetch(
        """
        SELECT content, source_type, source_url, service_name, metadata
        FROM embeddings
        WHERE content ILIKE $1
        ORDER BY last_updated DESC
        LIMIT 15
        """,
        f"%{query}%",
    )
    if rows:
        return rows

    # AND keywords
    keywords = [w.strip() for w in query.split() if len(w.strip()) > 2]
    if keywords:
        keywords = keywords[:5]
        conditions = " AND ".join(
            f"content ILIKE ${i + 1}" for i in range(len(keywords))
        )
        params = [f"%{kw}%" for kw in keywords]
        rows = await conn.fetch(
            f"""
            SELECT content, source_type, source_url, service_name, metadata
            FROM embeddings
            WHERE {conditions}
            ORDER BY last_updated DESC
            LIMIT 15
            """,
            *params,
        )

    if rows:
        return rows

    # OR keywords with ranking (last resort)
    keywords = [w.strip() for w in query.split() if len(w.strip()) > 2][:5]
    if keywords:
        or_conditions = " OR ".join(
            f"content ILIKE ${i + 1}" for i in range(len(keywords))
        )
        rank_expr = " + ".join(
            f"CASE WHEN content ILIKE ${i + 1} THEN 1 ELSE 0 END" for i in range(len(keywords))
        )
        params = [f"%{kw}%" for kw in keywords]
        rows = await conn.fetch(
            f"""
            SELECT content, source_type, source_url, service_name, metadata,
                   ({rank_expr}) AS relevance
            FROM embeddings
            WHERE {or_conditions}
            ORDER BY relevance DESC, last_updated DESC
            LIMIT 15
            """,
            *params,
        )

    return rows or []
