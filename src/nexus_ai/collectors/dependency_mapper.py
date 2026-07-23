"""Dependency Mapper — infers cross-service CALLS relationships from indexed data.

Analyzes:
1. Postman/Bruno collection URLs to identify which services call which other services
2. Swagger endpoint patterns to identify internal service-to-service paths
3. Known architectural dependencies (hardcoded from platform knowledge)

Creates CALLS relationships in Neo4j: (ServiceA)-[:CALLS]->(ServiceB)

Usage:
    python -m nexus_ai.collectors.dependency_mapper
"""

import asyncio
import json
import re
import time

from nexus_ai.db.neo4j import get_neo4j_driver, close_neo4j
from nexus_ai.db.postgres import get_pg_pool, close_pg

# Map service hostnames to service names (for URL pattern matching)
SERVICE_HOST_MAP = {
    "odx-dmx": "DMX",
    "odx-udx": "UDX",
    "odx-config-service": "Config Service",
    "odx-servicing-account": "Servicing Account",
    "odx-search-service": "Search Service",
    "odx-doc-uploader": "Doc Uploader",
    "odx-document-management-service": "Document Management Service",
    "odx-virus-scan-service": "Virus Scan Service",
    "odx-platform-integrator": "Platform Integrator",
    "odx-butler": "Butler",
    "odx-dmx-rbac": "RBAC",
    "odx-dmx-notifications": "DMX Notifications",
    "odx-rex-go": "Rules Engine (REX-GO)",
    "amt-transform-service": "Transform Service",
    "amt-activity-service": "Activity Service",
    "amt-report-storage-service": "Report Storage",
    "amt-headless-interpreter-service": "Headless Interpreter",
    "amt-product-config-bff": "Product Config BFF",
    "odx-api-gateway": "API Gateway",
    "amt-amortization-service": "Amortization Service (Morty)",
    "amt-vendor-service": "VIX (Vendor Integration)",
}

# Known architectural dependencies (from platform knowledge / Confluence docs)
# These are relationships that are hard to infer from URLs alone
KNOWN_DEPENDENCIES = [
    # DMX orchestrates decisions and calls multiple services
    ("DMX", "Rules Engine (REX-GO)"),
    ("DMX", "Rules Engine (REX-JAVA)"),
    ("DMX", "UDX"),
    ("DMX", "Config Service"),
    ("DMX", "DMX Notifications"),
    ("DMX", "Servicing Account"),
    ("DMX", "Transform Service"),
    # API Gateway routes to services
    ("API Gateway", "DMX"),
    ("API Gateway", "Servicing Account"),
    ("API Gateway", "Search Service"),
    ("API Gateway", "Doc Uploader"),
    # UDX calls vendor services
    ("UDX", "VIX (Vendor Integration)"),
    ("UDX", "Platform Integrator"),
    # Doc management flow
    ("Doc Uploader", "Virus Scan Service"),
    ("Doc Uploader", "Document Management Service"),
    # Headless interpreter uses DMX
    ("Headless Interpreter", "DMX"),
    ("Headless Interpreter", "Config Service"),
    # Customer Interpreter uses headless
    ("Customer Interpreter App", "Headless Interpreter"),
    ("Customer Interpreter App", "Config Service"),
    # Search aggregates from multiple sources
    ("Search Service", "Servicing Account"),
    ("Search Service", "DMX"),
    # Activity service tracks queue manager events
    ("Activity Service", "DMX"),
    # Product Config BFF
    ("Product Config BFF", "Config Service"),
    # Report storage
    ("Report Storage", "Doc Uploader"),
    # All services use Config Service
    ("RBAC", "Config Service"),
    ("Butler", "DMX"),
    ("Butler", "UDX"),
    ("Butler", "Servicing Account"),
]


async def infer_from_postman_urls() -> list[tuple[str, str]]:
    """Infer service dependencies from Postman/Bruno collection URLs.

    Logic: If collection X contains a URL pointing to service Y's hostname,
    then the service that collection X belongs to CALLS service Y.
    """
    pool = await get_pg_pool()
    dependencies = []

    async with pool.acquire() as conn:
        # Get all indexed Postman/Bruno requests with their service names and URLs
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
            # Also check for service host patterns in URL fields
            url_match = re.search(r'URL:\s*(.+)', content)
            if url_match:
                urls.append(url_match.group(1).strip())

            for url in urls:
                # Match against known service hostnames
                for host_pattern, target_service in SERVICE_HOST_MAP.items():
                    if host_pattern in url.lower():
                        # Don't create self-referencing dependency
                        if source_service and source_service.lower() != target_service.lower():
                            dependencies.append((source_service, target_service))
                        break

    # Deduplicate
    return list(set(dependencies))


async def store_dependencies(dependencies: list[tuple[str, str]]) -> int:
    """Store CALLS relationships in Neo4j."""
    driver = await get_neo4j_driver()
    stored = 0

    async with driver.session() as session:
        for caller, callee in dependencies:
            # Prefer exact match, fall back to CONTAINS
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

    # 2. Add known architectural dependencies
    print(f"\n2. Adding {len(KNOWN_DEPENDENCIES)} known architectural dependencies...")

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
        "known_architectural": len(KNOWN_DEPENDENCIES),
        "total_unique": len(all_deps),
        "stored_in_neo4j": stored,
        "duration_seconds": round(duration, 1),
    }

    print(f"\n{'=' * 60}")
    print(f"Done in {summary['duration_seconds']}s")
    print(f"  From URLs: {len(url_deps)}")
    print(f"  Known deps: {len(KNOWN_DEPENDENCIES)}")
    print(f"  Stored: {stored} relationships")
    print(f"{'=' * 60}")

    return summary


async def verify_dependencies():
    """Show dependency graph summary."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        # Count total relationships
        result = await session.run("MATCH ()-[r:CALLS]->() RETURN count(r) AS count")
        record = await result.single()
        print(f"\nTotal CALLS relationships: {record['count']}")

        # Most depended-on services (most incoming CALLS)
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

        # Services with most dependencies (most outgoing CALLS)
        result = await session.run(
            """
            MATCH (caller:Service)-[:CALLS]->(target:Service)
            RETURN caller.name AS service, count(target) AS dep_count
            ORDER BY dep_count DESC
            LIMIT 10
            """
        )
        print("\nServices with most dependencies (call the most other services):")
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
