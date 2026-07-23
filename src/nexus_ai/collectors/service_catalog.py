"""Service Catalog Loader — loads all 37 services into Neo4j from fixture JSON.

Usage:
    python -m nexus_ai.collectors.service_catalog
"""

import asyncio
import json
from pathlib import Path

from nexus_ai.db.neo4j import get_neo4j_driver, close_neo4j
from nexus_ai.db.schema import apply_schema

# Resolve catalog path — works both in Docker (/app/data/) and locally
_pkg_root = Path(__file__).parent.parent.parent.parent
_docker_path = Path("/app/data/service_catalog.json")
_local_path = _pkg_root / "data" / "service_catalog.json"
CATALOG_PATH = _docker_path if _docker_path.exists() else _local_path


async def load_service_catalog(catalog_path: Path = CATALOG_PATH) -> dict:
    """Load the service catalog JSON and populate Neo4j with Service and Team nodes.

    Returns a summary dict with counts.
    """
    with open(catalog_path) as f:
        catalog = json.load(f)

    driver = await get_neo4j_driver()

    # Apply schema constraints first
    await apply_schema()

    teams_created = 0
    services_created = 0

    async with driver.session() as session:
        # Create Team nodes
        for team_name in catalog["teams"]:
            result = await session.run(
                "MERGE (t:Team {name: $name}) RETURN t.name AS name",
                name=team_name,
            )
            record = await result.single()
            if record:
                teams_created += 1

        # Create Service nodes with OWNED_BY relationships
        for svc in catalog["services"]:
            result = await session.run(
                """
                MERGE (s:Service {name: $name})
                SET s.language = $language,
                    s.has_swagger = $has_swagger,
                    s.swagger_url = $swagger_url,
                    s.repo = $repo
                WITH s
                MATCH (t:Team {name: $owner})
                MERGE (s)-[:OWNED_BY]->(t)
                RETURN s.name AS name
                """,
                name=svc["name"],
                language=svc["language"],
                has_swagger=svc["has_swagger"],
                swagger_url=svc.get("swagger_url", ""),
                repo=svc.get("repo", ""),
                owner=svc["owner"],
            )
            record = await result.single()
            if record:
                services_created += 1

    summary = {
        "teams_created": teams_created,
        "services_created": services_created,
        "total_services_in_catalog": len(catalog["services"]),
    }
    print(f"✓ Loaded {services_created} services across {teams_created} teams into Neo4j")
    return summary


async def verify_catalog() -> dict:
    """Run verification queries against the loaded catalog."""
    driver = await get_neo4j_driver()

    async with driver.session() as session:
        # Total services
        result = await session.run("MATCH (s:Service) RETURN count(s) AS count")
        record = await result.single()
        total_services = record["count"]

        # Services with ownership
        result = await session.run(
            "MATCH (s:Service)-[:OWNED_BY]->(t:Team) RETURN count(s) AS count"
        )
        record = await result.single()
        owned_services = record["count"]

        # Count per team
        result = await session.run(
            """
            MATCH (s:Service)-[:OWNED_BY]->(t:Team)
            RETURN t.name AS team, count(s) AS service_count
            ORDER BY service_count DESC
            """
        )
        team_counts = {}
        async for record in result:
            team_counts[record["team"]] = record["service_count"]

        # Services with swagger
        result = await session.run(
            "MATCH (s:Service {has_swagger: true}) RETURN count(s) AS count"
        )
        record = await result.single()
        swagger_count = record["count"]

    verification = {
        "total_services": total_services,
        "owned_services": owned_services,
        "team_counts": team_counts,
        "services_with_swagger": swagger_count,
    }

    print(f"\n--- Verification ---")
    print(f"Total services: {total_services}")
    print(f"Services with ownership: {owned_services}")
    print(f"Services with Swagger: {swagger_count}")
    print(f"\nPer team:")
    for team, count in team_counts.items():
        print(f"  {team}: {count}")

    return verification


async def main():
    """Load catalog and verify."""
    try:
        await load_service_catalog()
        await verify_catalog()
    finally:
        await close_neo4j()


if __name__ == "__main__":
    asyncio.run(main())
