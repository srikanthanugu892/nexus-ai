"""DB Schema Collector — introspects PostgreSQL databases via Vault dynamic credentials.

Fetches table/column information from information_schema and stores it as
searchable embeddings for the agent's find_database_info tool.

Configure which databases to introspect in DB_ROLE_MAP (tools/db_tools.py).
"""

import asyncio
import json

import asyncpg
import httpx

from nexus_ai.config import settings
from nexus_ai.db.postgres import get_pg_pool
from nexus_ai.tools.db_tools import DB_ROLE_MAP, ENV_CONFIG, DEFAULT_ENV

_TLS_VERIFY = settings.tls_verify

# Map services to their GitHub repos for cross-referencing (optional)
SERVICE_REPO_MAP = {
    "Order Service": "your-org/order-service",
    "User Service": "your-org/user-service",
    "Payment Service": "your-org/payment-service",
}


async def collect_db_schemas(environment: str | None = None) -> dict:
    """Collect table/column schemas from all configured databases.

    For each database:
    1. Get temporary credentials from Vault
    2. Query information_schema for tables and columns
    3. Store as searchable chunks in pgvector

    Returns summary stats.
    """
    env = environment or DEFAULT_ENV
    if env not in ENV_CONFIG:
        return {"error": f"Unknown environment: {env}"}

    vault_host = ENV_CONFIG[env]["vault"]
    db_host = ENV_CONFIG[env]["db"]

    # Get Vault token
    vault_token = settings.vault_token
    if not vault_token:
        import os
        try:
            with open(os.path.expanduser("~/.vault-token")) as f:
                vault_token = f.read().strip()
        except FileNotFoundError:
            return {"error": "No Vault token available. Run: vault login"}

    pool = await get_pg_pool()
    stats = {"databases_scanned": 0, "tables_found": 0, "chunks_stored": 0}

    for db_name, vault_role in DB_ROLE_MAP.items():
        try:
            # Get dynamic credentials from Vault
            async with httpx.AsyncClient(verify=_TLS_VERIFY, timeout=10.0) as client:
                resp = await client.get(
                    f"https://{vault_host}/v1/database/creds/{vault_role}",
                    headers={"X-Vault-Token": vault_token},
                )
                if resp.status_code != 200:
                    print(f"  ✗ {db_name}: Vault creds failed ({resp.status_code})")
                    continue
                creds = resp.json().get("data", {})

            # Connect and query schema
            conn = await asyncpg.connect(
                host=db_host, port=5432, database=db_name,
                user=creds["username"], password=creds["password"],
                timeout=10.0,
            )

            # Get tables and their columns
            rows = await conn.fetch("""
                SELECT t.table_schema, t.table_name, 
                       string_agg(c.column_name || ' ' || c.data_type, ', ' ORDER BY c.ordinal_position) AS columns
                FROM information_schema.tables t
                JOIN information_schema.columns c ON t.table_name = c.table_name AND t.table_schema = c.table_schema
                WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND t.table_type = 'BASE TABLE'
                GROUP BY t.table_schema, t.table_name
                ORDER BY t.table_schema, t.table_name
            """)

            await conn.close()

            # Store each table as a searchable chunk
            async with pool.acquire() as pgconn:
                for row in rows:
                    schema = row["table_schema"]
                    table = row["table_name"]
                    columns = row["columns"]

                    content = f"Database: {db_name} | Schema: {schema} | Table: {table}\nColumns: {columns}"

                    # Upsert (avoid duplicates on re-run)
                    await pgconn.execute("""
                        INSERT INTO embeddings (content, source_type, service_name, metadata, source_url)
                        VALUES ($1, 'db_schema', $2, $3, $4)
                        ON CONFLICT DO NOTHING
                    """,
                        content,
                        db_name,
                        json.dumps({"schema": schema, "table": table, "database": db_name}),
                        f"vault://{vault_host}/database/creds/{vault_role}",
                    )
                    stats["chunks_stored"] += 1

                stats["tables_found"] += len(rows)

            stats["databases_scanned"] += 1
            print(f"  ✓ {db_name}: {len(rows)} tables")

        except Exception as e:
            print(f"  ✗ {db_name}: {type(e).__name__}: {e}")

    print(f"\n✓ DB schema collection complete: {stats['databases_scanned']} databases, {stats['tables_found']} tables")
    return stats


async def main():
    await collect_db_schemas()


if __name__ == "__main__":
    asyncio.run(main())
