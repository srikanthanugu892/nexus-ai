"""Database schema tools — search tables/columns and query live databases.

Uses Vault dynamic credentials for secure, short-lived DB access.
Configure databases and their Vault roles in ENV_CONFIG and DB_ROLE_MAP.
"""

import json
import os
import re

import httpx

from nexus_ai.config import settings
from nexus_ai.db.postgres import get_pg_pool

# TLS verification setting
_TLS_VERIFY = settings.tls_verify

# Environment configuration — maps environment names to their Vault and DB hosts.
# Customize this for your infrastructure.
ENV_CONFIG = {
    "dev": {"vault": "vault.dev.example.com", "db": "db.dev.example.com"},
    "staging": {"vault": "vault.staging.example.com", "db": "db.staging.example.com"},
    "production": {"vault": "vault.prod.example.com", "db": "db.prod.example.com"},
}

# Default environment
DEFAULT_ENV = os.environ.get("VAULT_ENV", "dev")
DB_PORT = 5432

# DB name → Vault role mapping (customize for your services)
DB_ROLE_MAP = {
    "orders": "order-service-db",
    "users": "user-service-db",
    "payments": "payment-service-db",
    "inventory": "inventory-service-db",
    "notifications": "notification-service-db",
    "analytics": "analytics-service-db",
}

# Token cache per environment
_cached_tokens: dict[str, str] = {}
_cached_tokens_verified: dict[str, float] = {}


async def _get_valid_vault_token(vault_host: str) -> str | None:
    """Get a valid Vault token, trying multiple sources.

    Order:
    1. Cached token (if verified within last 5 minutes)
    2. VAULT_TOKEN from .env / environment
    3. ~/.vault-token file
    4. AppRole login (fully automated, no human needed)
    """
    import time

    cache_key = vault_host

    # Use cache if recently verified
    if cache_key in _cached_tokens and (time.time() - _cached_tokens_verified.get(cache_key, 0)) < 300:
        return _cached_tokens[cache_key]

    # Collect candidate tokens
    candidates = []
    if settings.vault_token:
        candidates.append(settings.vault_token)

    # Read from ~/.vault-token
    for path in [os.path.expanduser("~/.vault-token")]:
        try:
            with open(path) as f:
                token = f.read().strip()
                if token and token not in candidates:
                    candidates.append(token)
        except (FileNotFoundError, PermissionError):
            pass

    # Validate each candidate
    for token in candidates:
        if await _verify_vault_token(vault_host, token):
            _cached_tokens[cache_key] = token
            _cached_tokens_verified[cache_key] = time.time()
            return token

    # AppRole fallback
    approle_token = await _approle_login(vault_host)
    if approle_token:
        _cached_tokens[cache_key] = approle_token
        _cached_tokens_verified[cache_key] = time.time()
        return approle_token

    return None


async def _verify_vault_token(vault_host: str, token: str) -> bool:
    """Check if a Vault token is still valid."""
    try:
        async with httpx.AsyncClient(verify=_TLS_VERIFY, timeout=5.0) as client:
            resp = await client.get(
                f"https://{vault_host}/v1/auth/token/lookup-self",
                headers={"X-Vault-Token": token},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                ttl = data.get("ttl", 0)
                return ttl > 60
    except Exception:
        pass
    return False


async def _approle_login(vault_host: str) -> str | None:
    """Login via AppRole if credentials are configured.

    Set VAULT_APPROLE_ROLE_ID and VAULT_APPROLE_SECRET_ID in .env.
    """
    role_id = os.environ.get("VAULT_APPROLE_ROLE_ID", "")
    secret_id = os.environ.get("VAULT_APPROLE_SECRET_ID", "")

    if not role_id or not secret_id:
        return None

    try:
        async with httpx.AsyncClient(verify=_TLS_VERIFY, timeout=5.0) as client:
            resp = await client.post(
                f"https://{vault_host}/v1/auth/approle/login",
                json={"role_id": role_id, "secret_id": secret_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("auth", {}).get("client_token")
    except Exception:
        pass
    return None


async def find_database_info(query: str) -> str:
    """Search database schemas for tables or columns matching the query.

    Uses vector search (semantic) with text fallback.
    """
    from nexus_ai.tools.embeddings import embed_text

    pool = await get_pg_pool()
    results = []

    async with pool.acquire() as conn:
        # Try vector search first
        has_embeddings = await conn.fetchval(
            "SELECT count(*) FROM embeddings WHERE source_type = 'db_schema' AND embedding IS NOT NULL"
        )

        if has_embeddings > 0:
            query_vec = await embed_text(query)
            if query_vec:
                vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
                rows = await conn.fetch(
                    """
                    SELECT content, service_name, metadata
                    FROM embeddings
                    WHERE source_type = 'db_schema' AND embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT 10
                    """,
                    vec_str,
                )
                for row in rows:
                    results.append({
                        "content": row["content"],
                        "service": row["service_name"],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    })

        # Text fallback
        if not results:
            keywords = [w.strip() for w in query.split() if len(w.strip()) > 2]
            if keywords:
                keywords = keywords[:4]
                conditions = " AND ".join(
                    f"content ILIKE ${i + 1}" for i in range(len(keywords))
                )
                params = [f"%{kw}%" for kw in keywords]
                rows = await conn.fetch(
                    f"""
                    SELECT content, service_name, metadata
                    FROM embeddings
                    WHERE source_type = 'db_schema' AND {conditions}
                    ORDER BY last_updated DESC
                    LIMIT 10
                    """,
                    *params,
                )
                for row in rows:
                    results.append({
                        "content": row["content"],
                        "service": row["service_name"],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    })

    if not results:
        return json.dumps({
            "results": [],
            "note": f"No database tables/columns found matching '{query}'.",
            "available_databases": list(DB_ROLE_MAP.keys()),
        })

    return json.dumps({"results": results, "count": len(results)})


async def query_database(database: str, sql: str, environment: str | None = None) -> str:
    """Run a read-only SQL query against a live database via Vault dynamic credentials.

    Only SELECT queries are allowed (read-only access enforced at DB transaction level).
    Results are automatically limited to 50 rows and sensitive columns are redacted.
    """
    env = environment or DEFAULT_ENV
    if env not in ENV_CONFIG:
        return json.dumps({"error": f"Unknown environment '{env}'", "available": list(ENV_CONFIG.keys())})

    vault_host = ENV_CONFIG[env]["vault"]
    db_host = ENV_CONFIG[env]["db"]

    if database not in DB_ROLE_MAP:
        return json.dumps({
            "error": f"Unknown database '{database}'",
            "available_databases": list(DB_ROLE_MAP.keys()),
        })

    # Security: only allow SELECT queries
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return json.dumps({"error": "Only SELECT queries are allowed (read-only access)."})

    # Enforce row limit
    if "LIMIT" not in sql_upper:
        sql = sql.rstrip(";") + " LIMIT 50"

    # Get Vault token
    vault_token = await _get_valid_vault_token(vault_host)
    if not vault_token:
        return json.dumps({
            "error": "Vault token expired or missing. Cannot connect to databases.",
            "fix": f"Authenticate with Vault: vault login -method=oidc -address=https://{vault_host}",
        })

    vault_role = DB_ROLE_MAP[database]

    try:
        async with httpx.AsyncClient(verify=_TLS_VERIFY, timeout=10.0) as client:
            resp = await client.get(
                f"https://{vault_host}/v1/database/creds/{vault_role}",
                headers={"X-Vault-Token": vault_token},
            )
            if resp.status_code != 200:
                return json.dumps({"error": f"Vault credential fetch failed: HTTP {resp.status_code}"})

            creds = resp.json().get("data", {})

        # Connect and query with read-only enforcement
        import asyncpg
        conn = await asyncpg.connect(
            host=db_host, port=DB_PORT, database=database,
            user=creds["username"], password=creds["password"],
            timeout=10.0,
            server_settings={"default_transaction_read_only": "on"},
        )

        rows = await conn.fetch(sql)
        await conn.close()

        # Convert to JSON, redacting sensitive columns
        SENSITIVE_COLUMNS = {"password", "secret", "token", "api_key", "client_secret", "access_token", "refresh_token", "private_key"}
        results = []
        for row in rows:
            row_dict = {}
            for k, v in dict(row).items():
                if any(s in k.lower() for s in SENSITIVE_COLUMNS) and v is not None:
                    row_dict[k] = "[REDACTED]"
                else:
                    row_dict[k] = str(v) if v is not None else None
            results.append(row_dict)

        return json.dumps({
            "database": database,
            "query": sql,
            "rows": results,
            "row_count": len(results),
        })

    except Exception as e:
        return json.dumps({"error": f"Query failed: {type(e).__name__}: {str(e)}"})
