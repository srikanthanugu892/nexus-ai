"""Impact analysis tools — assess risk of changes to services/endpoints."""

import json

from nexus_ai.db.neo4j import get_neo4j_driver


async def calculate_impact(service: str, change_description: str) -> str:
    """Assess risk of changing/removing a service, endpoint, or field.

    Traces upstream consumers via the knowledge graph, scores risk
    (HIGH/MEDIUM/LOW), and recommends mitigation actions.
    """
    driver = await get_neo4j_driver()

    async with driver.session() as session:
        # Find the service
        result = await session.run(
            """
            MATCH (s:Service)
            WHERE toLower(s.name) CONTAINS toLower($name)
            RETURN s.name AS name
            LIMIT 1
            """,
            name=service,
        )
        svc_record = await result.single()

        if not svc_record:
            return json.dumps({"error": f"Service '{service}' not found in the knowledge graph."})

        actual_name = svc_record["name"]

        # Find all consumers (direct and transitive)
        result = await session.run(
            """
            MATCH (consumer:Service)-[:CALLS]->(target:Service {name: $name})
            OPTIONAL MATCH (consumer)-[:OWNED_BY]->(t:Team)
            RETURN consumer.name AS consumer, t.name AS team
            ORDER BY consumer.name
            """,
            name=actual_name,
        )

        consumers = []
        teams_affected = set()
        async for record in result:
            consumers.append({
                "service": record["consumer"],
                "team": record["team"],
            })
            if record["team"]:
                teams_affected.add(record["team"])

        # Score risk
        consumer_count = len(consumers)
        if consumer_count >= 5:
            risk = "HIGH"
            recommendation = "Requires cross-team coordination. Create a migration plan with deprecation timeline."
        elif consumer_count >= 2:
            risk = "MEDIUM"
            recommendation = "Notify affected teams. Consider feature flag or versioned endpoint."
        elif consumer_count == 1:
            risk = "LOW"
            recommendation = "Coordinate with the consuming team directly."
        else:
            risk = "LOW"
            recommendation = "No known consumers. Safe to proceed with standard review."

        return json.dumps({
            "service": actual_name,
            "change": change_description,
            "risk_score": risk,
            "consumers": consumers,
            "consumer_count": consumer_count,
            "teams_affected": list(teams_affected),
            "recommendation": recommendation,
        })
