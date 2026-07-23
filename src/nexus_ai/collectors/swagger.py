"""Swagger/OpenAPI Collector — fetches live specs, parses endpoints, stores in Neo4j + pgvector.

Usage:
    python -m nexus_ai.collectors.swagger
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import yaml

from nexus_ai.config import settings
from nexus_ai.db.neo4j import get_neo4j_driver, close_neo4j
from nexus_ai.db.postgres import get_pg_pool, close_pg

# Resolve config path
_docker_path = Path("/app/data/swagger_sources.json")
_local_path = Path(__file__).parent.parent.parent.parent / "data" / "swagger_sources.json"
SOURCES_PATH = _docker_path if _docker_path.exists() else _local_path

# Timeout for fetching specs (some services may be slow or down)
FETCH_TIMEOUT = 15.0


async def fetch_spec(client: httpx.AsyncClient, source: dict) -> dict | None:
    """Fetch and parse a single OpenAPI spec. Returns parsed dict or None on failure."""
    url = source["url"]
    service = source["service"]
    fmt = source["format"]

    try:
        resp = await client.get(url, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()

        if fmt == "yaml":
            spec = yaml.safe_load(resp.text)
        else:
            spec = resp.json()

        print(f"  ✓ {service}: fetched ({len(resp.text)} bytes)")
        return spec

    except httpx.TimeoutException:
        print(f"  ✗ {service}: timeout ({url})")
        return None
    except httpx.HTTPStatusError as e:
        print(f"  ✗ {service}: HTTP {e.response.status_code} ({url})")
        return None
    except Exception as e:
        print(f"  ✗ {service}: {type(e).__name__}: {e}")
        return None


def extract_endpoints(spec: dict, service_name: str) -> list[dict]:
    """Extract API endpoints from an OpenAPI spec."""
    endpoints = []
    paths = spec.get("paths", {})

    for path, methods in paths.items():
        for method, details in methods.items():
            if method in ("get", "post", "put", "delete", "patch"):
                # Build description from summary, description, and tags
                summary = details.get("summary", "")
                description = details.get("description", "")
                tags = details.get("tags", [])
                operation_id = details.get("operationId", "")

                # Extract request/response field names
                parameters = []
                for param in details.get("parameters", []):
                    parameters.append({
                        "name": param.get("name", ""),
                        "in": param.get("in", ""),
                        "required": param.get("required", False),
                    })

                # Extract request body fields (top-level schema properties)
                request_fields = []
                request_body = details.get("requestBody", {})
                if request_body:
                    content = request_body.get("content", {})
                    for content_type, schema_info in content.items():
                        schema = schema_info.get("schema", {})
                        # Resolve $ref to get properties (simplified — doesn't follow deep refs)
                        props = schema.get("properties", {})
                        for field_name, field_info in props.items():
                            request_fields.append({
                                "name": field_name,
                                "type": field_info.get("type", "object"),
                                "location": "request_body",
                            })

                endpoints.append({
                    "service": service_name,
                    "path": path,
                    "method": method.upper(),
                    "summary": summary,
                    "description": description,
                    "tags": tags,
                    "operation_id": operation_id,
                    "parameters": parameters,
                    "request_fields": request_fields,
                })

    return endpoints


async def store_in_neo4j(endpoints: list[dict], service_name: str):
    """Store API endpoints as nodes in Neo4j with EXPOSES_API relationships."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        for ep in endpoints:
            await session.run(
                """
                MATCH (s:Service {name: $service})
                MERGE (a:API {path: $path, method: $method, service: $service})
                SET a.summary = $summary,
                    a.description = $description,
                    a.tags = $tags,
                    a.operation_id = $operation_id
                MERGE (s)-[:EXPOSES_API]->(a)
                """,
                service=service_name,
                path=ep["path"],
                method=ep["method"],
                summary=ep["summary"],
                description=ep["description"],
                tags=ep["tags"],
                operation_id=ep["operation_id"],
            )

            # Store request fields
            for field in ep["request_fields"]:
                await session.run(
                    """
                    MATCH (a:API {path: $path, method: $method, service: $service})
                    MERGE (f:Field {name: $field_name, api_path: $path, api_method: $method, api_service: $service})
                    SET f.type = $field_type, f.location = $location
                    MERGE (a)-[:HAS_FIELD]->(f)
                    """,
                    path=ep["path"],
                    method=ep["method"],
                    service=service_name,
                    field_name=field["name"],
                    field_type=field["type"],
                    location=field["location"],
                )


async def store_in_pgvector(endpoints: list[dict], service_name: str, source_url: str):
    """Store API endpoint descriptions in pgvector for semantic search.

    Note: Stores text content without embeddings for now (text search fallback).
    Embeddings will be generated when the embedding model is wired up.
    """
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        for ep in endpoints:
            # Build searchable content chunk per endpoint
            content_parts = [
                f"Service: {service_name}",
                f"API: {ep['method']} {ep['path']}",
            ]
            if ep["summary"]:
                content_parts.append(f"Summary: {ep['summary']}")
            if ep["description"]:
                content_parts.append(f"Description: {ep['description']}")
            if ep["tags"]:
                content_parts.append(f"Tags: {', '.join(ep['tags'])}")
            if ep["parameters"]:
                param_names = [p["name"] for p in ep["parameters"]]
                content_parts.append(f"Parameters: {', '.join(param_names)}")
            if ep["request_fields"]:
                field_names = [f["name"] for f in ep["request_fields"]]
                content_parts.append(f"Request fields: {', '.join(field_names)}")

            content = "\n".join(content_parts)

            metadata = {
                "path": ep["path"],
                "method": ep["method"],
                "tags": ep["tags"],
                "operation_id": ep["operation_id"],
            }

            await conn.execute(
                """
                INSERT INTO embeddings (content, source_type, source_url, service_name, metadata)
                VALUES ($1, 'swagger', $2, $3, $4)
                ON CONFLICT DO NOTHING
                """,
                content,
                source_url,
                service_name,
                json.dumps(metadata),
            )


async def run_collector() -> dict:
    """Run the full Swagger collector pipeline."""
    print("=" * 60)
    print("Swagger/OpenAPI Collector")
    print("=" * 60)

    with open(SOURCES_PATH) as f:
        config = json.load(f)

    sources = config["sources"]
    print(f"\nFetching specs from {len(sources)} services...\n")

    start_time = time.time()
    total_endpoints = 0
    successful = 0
    failed = 0

    async with httpx.AsyncClient(verify=settings.tls_verify) as client:
        for source in sources:
            spec = await fetch_spec(client, source)
            if spec is None:
                failed += 1
                continue

            # Extract endpoints
            endpoints = extract_endpoints(spec, source["service"])
            if not endpoints:
                print(f"  ⚠ {source['service']}: no endpoints found in spec")
                failed += 1
                continue

            # Store in Neo4j + pgvector
            await store_in_neo4j(endpoints, source["service"])
            await store_in_pgvector(endpoints, source["service"], source["url"])

            total_endpoints += len(endpoints)
            successful += 1
            print(f"    → stored {len(endpoints)} endpoints")

    duration = time.time() - start_time

    summary = {
        "total_sources": len(sources),
        "successful": successful,
        "failed": failed,
        "total_endpoints": total_endpoints,
        "duration_seconds": round(duration, 1),
    }

    print(f"\n{'=' * 60}")
    print(f"Done in {summary['duration_seconds']}s")
    print(f"  Successful: {successful}/{len(sources)} services")
    print(f"  Failed: {failed}/{len(sources)} services")
    print(f"  Total endpoints stored: {total_endpoints}")
    print(f"{'=' * 60}")

    return summary


async def verify_collector():
    """Verify collector output in Neo4j and pgvector."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        # Count API nodes
        result = await session.run("MATCH (a:API) RETURN count(a) AS count")
        record = await result.single()
        api_count = record["count"]

        # Count services with APIs
        result = await session.run(
            "MATCH (s:Service)-[:EXPOSES_API]->(a:API) RETURN s.name AS service, count(a) AS api_count ORDER BY api_count DESC"
        )
        print(f"\nNeo4j: {api_count} API nodes total")
        print("\nAPIs per service:")
        async for record in result:
            print(f"  {record['service']}: {record['api_count']} endpoints")

    # Check pgvector
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM embeddings WHERE source_type = 'swagger'")
        print(f"\npgvector: {count} swagger chunks indexed")

        # Test a search
        sample = await conn.fetch(
            "SELECT service_name, content FROM embeddings WHERE source_type = 'swagger' AND content ILIKE '%application%' LIMIT 3"
        )
        if sample:
            print(f"\nSample search for 'application':")
            for row in sample:
                first_line = row["content"].split("\n")[1]  # Skip "Service:" line
                print(f"  [{row['service_name']}] {first_line}")


async def main():
    """Run collector and verify."""
    try:
        await run_collector()
        await verify_collector()
    finally:
        await close_neo4j()
        await close_pg()


if __name__ == "__main__":
    asyncio.run(main())
