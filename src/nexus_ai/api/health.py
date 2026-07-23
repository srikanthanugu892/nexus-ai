"""Health check endpoint — verifies connectivity to Neo4j and PostgreSQL."""

from fastapi import APIRouter

from nexus_ai.db.neo4j import check_neo4j_connection
from nexus_ai.db.postgres import check_pg_connection

router = APIRouter()


@router.get("/health")
async def health_check():
    """Returns system health status including database connectivity."""
    neo4j_ok = await check_neo4j_connection()
    pg_ok = await check_pg_connection()

    status = "ok" if (neo4j_ok and pg_ok) else "degraded"

    return {
        "status": status,
        "neo4j": "connected" if neo4j_ok else "disconnected",
        "pgvector": "connected" if pg_ok else "disconnected",
    }
