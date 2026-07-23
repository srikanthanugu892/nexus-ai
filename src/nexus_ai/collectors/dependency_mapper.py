"""Dependency Mapper — infers cross-service CALLS relationships from indexed data.

Analyzes:
1. Postman/Bruno collection URLs to identify which services call which other services
2. Swagger endpoint patterns to identify internal service-to-service paths
3. Known architectural dependencies (from service catalog or manual configuration)

Creates CALLS relationships in Neo4j: (ServiceA)-[:CALLS]->(ServiceB)

Usage:
    python -m nexus_ai.collectors.dependency_mapper
"""

import asyncio
import json
import re
import time
from pathlib import Path

from nexus_ai.db.neo4j import get_neo4j_driver, close_neo4j
from nexus_ai.db.postgres import get_pg_pool, close_pg


def _load_known_dependencies() -> list[tuple[str, str]]:
    """Load dependency relationships from the service catalog JSON."""
    catalog_path = Path(__file__).parent.parent.parent.parent / "data" / "service_catalog.json"
    if not catalog_path.exists():
        return []

    with open(catalog_path) as f:
        catalog = json.load(f)

    return [(dep["from"], dep["to"]) for dep in catalog.get("dependencies", [])]


def _build_host_map() -> dict[str, str]:
    """Build hostname → service name map from the service catalog.

    Maps common URL patterns like 'order-service' to 'Order Service' for
    inferring dependencies from Postman/Bruno collection URLs.
    """
    catalog_path = Path(__file__).parent.parent.parent.parent / "data" / "service_catalog.json"
    if not catalog_path.exists():
        return {}

    with open(catalog_path) as f:
        catalog = json.load(f)

    host_map = {}
    for svc in catalog.get("services", []):
        # Generate possible hostname patterns from service name
        name = svc["name"]
        # "Order Service" → "order-service"
        slug = name.lower().replace(" ", "-")
        host_map[slug] = name
        # Also try without "service" suffix: "order-service" → "order"
        if slug.endswith("-service"):
            host_map[slug[:-8]] = name

    return host_map


# Load from catalog at module import
SERVICE_HOST_MAP = _build_host_map()
KNOWN_DEPENDENCIES = _load_known_dependencies()


async def infer_from_postman_urls() -> list[tuple[str, str]]:
    """Infer service dependencies from Postman/Bruno collection URLs.

    Logic: If collection X contains a URL pointing to service Y's hostname,
    then the service that collection X belongs to CALLS service Y.
    """
    pool = await get_pg_pool()
    dependencies = []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT service_name, content, metadata
            FROM embeddings
            WHERE source_type IN ('postman', 'bruno')
            AND content IS NOT NULL
            """
        )

        for row in rows:
            source_service = row["service_name"]
            content = row["content"] or ""

            # Extract URLs from the content
            urls = re.findall(r'https?://[^\s"\'<>]+', content)
            url_match = re.search(r'URL:\s*(.+)', content)
            if url_match:
                urls.append(url_match.group(1).strip())

            for url in urls:
                for host_pattern, target_service in SERVICE_HOST_MAP.items():
                    if host_pattern in url.lower():
                        if source_service and source_service.lower() != target_service.lower():
                            dependencies.append((source_service, target_service))
                        break

    return list(set(dependencies))


async def store_dependencies(dependencies: list[tuple[str, str]]) -> int:
    """Store CALLS relationships in Neo4j."""
    driver = await get_neo4j_driver()
    stored = 0

    async with driver.session() as session:
        for caller, callee in dependencies:
            result = await session.run(
                """
                MATCH (a:Service)
                WHERE a.name = $caller OR (NOT EXISTS {
                    MATCH (x:Service) WHERE x.name = $caller
                } AND toLower(a.name) CONTAINS toLower($caller))
                WITH a ORDER BY CASE WHEN a.name = $caller THEN 0 ELSE 1 END LIMIT 1
                MATCH (b:Service)
                WHERE b.name = $callee OR (NOT EXISTS {
                    MATCH (y:Service) WHERE y.name = $callee
                } AND toLower(b.name) CONTAINS toLower($callee))
                WITH a, b ORDER BY CASE WHEN b.name = $callee THEN 0 ELSE 1 END LIMIT 1
                MERGE (a)-[:CALLS]->(b)
                RETURN a.name AS caller, b.name AS callee
                """,
                caller=caller,
                callee=callee,
            )
            record = await result.single()
            if record:
                stored += 1

    return stored


async def run_mapper() -> dict:
    """Run the full dependency mapping pipeline."""
    print("=" * 60)
    print("Dependency Mapper")
    print("=" * 60)
    start_time = time.time()

    # 1. Infer from Postman/Bruno URLs
    print("\n1. Analyzing Postman/Bruno collection URLs...")
    url_deps = await infer_from_postman_urls()
    print(f"   Found {len(url_deps)} inferred dependencies from URLs")

    # 2. Load known architectural dependencies from catalog
    print(f"\n2. Loading {len(KNOWN_DEPENDENCIES)} known dependencies from service catalog...")

    # Combine all dependencies
    all_deps = list(set(url_deps + KNOWN_DEPENDENCIES))
    print(f"\n3. Total unique dependencies to store: {len(all_deps)}")

    # 3. Store in Neo4j
    print("\n4. Storing CALLS relationships in Neo4j...")
    stored = await store_dependencies(all_deps)
    print(f"   Stored {stored} CALLS relationships")

    duration = time.time() - start_time

    summary = {
        "inferred_from_urls": len(url_deps),
        "known_from_catalog": len(KNOWN_DEPENDENCIES),
        "total_unique": len(all_deps),
        "stored_in_neo4j": stored,
        "duration_seconds": round(duration, 1),
    }

    print(f"\n{'=' * 60}")
    print(f"Done in {summary['duration_seconds']}s")
    print(f"  From URLs: {len(url_deps)}")
    print(f"  From catalog: {len(KNOWN_DEPENDENCIES)}")
    print(f"  Stored: {stored} relationships")
    print(f"{'=' * 60}")

    return summary


async def verify_dependencies():
    """Show dependency graph summary."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run("MATCH ()-[r:CALLS]->() RETURN count(r) AS count")
        record = await result.single()
        print(f"\nTotal CALLS relationships: {record['count']}")

        result = await session.run(
            """
            MATCH (consumer:Service)-[:CALLS]->(target:Service)
            RETURN target.name AS service, count(consumer) AS consumer_count
            ORDER BY consumer_count DESC
            LIMIT 10
            """
        )
        print("\nMost depended-on services:")
        async for record in result:
            print(f"  {record['service']}: {record['consumer_count']} consumers")

        result = await session.run(
            """
            MATCH (caller:Service)-[:CALLS]->(target:Service)
            RETURN caller.name AS service, count(target) AS dep_count
            ORDER BY dep_count DESC
            LIMIT 10
            """
        )
        print("\nServices with most outgoing dependencies:")
        async for record in result:
            print(f"  {record['service']}: calls {record['dep_count']} services")


async def main():
    try:
        await run_mapper()
        await verify_dependencies()
    finally:
        await close_neo4j()
        await close_pg()


if __name__ == "__main__":
    asyncio.run(main())
