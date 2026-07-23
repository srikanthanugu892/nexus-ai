"""Interaction logging — stores agent Q&A for observability and prompt tuning."""

import json
import logging

from nexus_ai.db.postgres import get_pg_pool

logger = logging.getLogger(__name__)


async def log_interaction(
    question: str,
    tool_calls: list[dict],
    final_answer: str,
    model: str,
    total_tokens: int,
    duration_ms: int,
    error: str | None = None,
) -> None:
    """Log a complete agent interaction to PostgreSQL for observability."""
    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO interaction_logs (question, tool_calls, final_answer, model, total_tokens, duration_ms, error)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                question,
                json.dumps(tool_calls),
                final_answer,
                model,
                total_tokens,
                duration_ms,
                error,
            )
    except Exception as e:
        logger.debug(f"Failed to log interaction: {e}")
