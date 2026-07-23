"""Graph tools — query Neo4j knowledge graph for service/team/dependency information."""

import json

from nexus_ai.db.neo4j import get_neo4j_driver


async def find_service(name: str) -> str:
    """Find a service by name (case-insensitive fuzzy match)."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (s:Service)
            WHERE toLower(s.name) = toLower($name)
               OR toLower(s.name) CONTAINS toLower($name)
            OPTIONAL MATCH (s)-[:OWNED_BY]->(t:Team)
            RETURN s.name AS name, s.language AS language, s.has_swagger AS has_swagger,
                   s.swagger_url AS swagger_url, s.repo AS repo, t.name AS owner
            ORDER BY CASE WHEN toLower(s.name) = toLower($name) THEN 0 ELSE 1 END
            LIMIT 5
            """,
            name=name,
        )
        services = []
        async for record in result:
            svc = {
                "name": record["name"],
                "language": record["language"],
                "owner": record["owner"],
                "has_swagger": record["has_swagger"],
                "repo": record["repo"],
            }
            if record["swagger_url"]:
                svc["swagger_url"] = record["swagger_url"]
            services.append(svc)

        if not services:
            return json.dumps({"error": f"No service found matching '{name}'", "suggestion": "Try a different name or check available services."})

        return json.dumps({"services": services, "count": len(services)})


async def find_owner(service_name: str) -> str:
    """Find which team owns a service."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (s:Service)-[:OWNED_BY]->(t:Team)
            WHERE toLower(s.name) = toLower($name)
               OR toLower(s.name) CONTAINS toLower($name)
            RETURN s.name AS service, t.name AS team
            ORDER BY CASE WHEN toLower(s.name) = toLower($name) THEN 0 ELSE 1 END
            LIMIT 1
            """,
            name=service_name,
        )
        record = await result.single()

        if not record:
            return json.dumps({"error": f"No service found matching '{service_name}'"})

        return json.dumps({
            "service": record["service"],
            "owner": record["team"],
        })


async def list_team_services(team_name: str) -> str:
    """List all services owned by a team."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (s:Service)-[:OWNED_BY]->(t:Team)
            WHERE toLower(t.name) CONTAINS toLower($team_name)
            RETURN s.name AS name, s.language AS language, s.has_swagger AS has_swagger, t.name AS team
            ORDER BY s.name
            """,
            team_name=team_name,
        )
        services = []
        team = None
        async for record in result:
            team = record["team"]
            services.append({
                "name": record["name"],
                "language": record["language"],
                "has_swagger": record["has_swagger"],
            })

        if not services:
            return json.dumps({"error": f"No team found matching '{team_name}'"})

        return json.dumps({
            "team": team,
            "services": services,
            "count": len(services),
        })


async def find_api_consumers(service_name: str) -> str:
    """Find services that consume (call) the given service's APIs."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        check = await session.run(
            "MATCH (s:Service) WHERE toLower(s.name) CONTAINS toLower($name) RETURN s.name AS name LIMIT 1",
            name=service_name,
        )
        svc_record = await check.single()

        if not svc_record:
            return json.dumps({"error": f"No service found matching '{service_name}'"})

        actual_name = svc_record["name"]

        result = await session.run(
            """
            MATCH (consumer:Service)-[:CALLS]->(target:Service {name: $name})
            OPTIONAL MATCH (consumer)-[:OWNED_BY]->(t:Team)
            RETURN consumer.name AS consumer, t.name AS consumer_team
            ORDER BY consumer.name
            """,
            name=actual_name,
        )
        consumers = []
        async for record in result:
            consumers.append({
                "service": record["consumer"],
                "team": record["consumer_team"],
            })

        return json.dumps({
            "target_service": actual_name,
            "consumers": consumers,
            "consumer_count": len(consumers),
        })
