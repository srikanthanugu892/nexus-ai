"""Neo4j graph schema — constraints, indexes, and node/relationship definitions.

Node types:
    - Service: A microservice (name, language, has_swagger, description)
    - Team: An owning team (name)
    - Repository: A GitHub repo (name, url, org)
    - API: An API endpoint (path, method, description, spec_type)
    - Database: A PostgreSQL database (name, vault_role, host)
    - Field: A request/response field (name, data_type, location)

Relationship types:
    - (Service)-[:OWNED_BY]->(Team)
    - (Service)-[:HAS_REPO]->(Repository)
    - (Service)-[:EXPOSES_API]->(API)
    - (Service)-[:USES_DATABASE]->(Database)
    - (Service)-[:CALLS]->(Service)
    - (API)-[:HAS_FIELD]->(Field)
    - (API)-[:CONSUMED_BY]->(Service)
    - (Service)-[:HAD_INCIDENT]->(Incident)
    - (Database)-[:HAS_TABLE]->(Table)
    - (Table)-[:HAS_COLUMN]->(Column)
"""

from nexus_ai.db.neo4j import get_neo4j_driver

# Schema constraints and indexes — idempotent (IF NOT EXISTS)
SCHEMA_STATEMENTS = [
    # Uniqueness constraints
    "CREATE CONSTRAINT service_name IF NOT EXISTS FOR (s:Service) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT team_name IF NOT EXISTS FOR (t:Team) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT repository_name IF NOT EXISTS FOR (r:Repository) REQUIRE r.full_name IS UNIQUE",
    "CREATE CONSTRAINT database_name IF NOT EXISTS FOR (d:Database) REQUIRE d.name IS UNIQUE",
    # Indexes for frequent lookups
    "CREATE INDEX service_language IF NOT EXISTS FOR (s:Service) ON (s.language)",
    "CREATE INDEX api_path IF NOT EXISTS FOR (a:API) ON (a.path)",
    "CREATE INDEX api_method IF NOT EXISTS FOR (a:API) ON (a.method)",
    "CREATE INDEX field_name IF NOT EXISTS FOR (f:Field) ON (f.name)",
]


async def apply_schema():
    """Apply all constraints and indexes to Neo4j. Safe to run multiple times."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        for stmt in SCHEMA_STATEMENTS:
            await session.run(stmt)
    print(f"✓ Applied {len(SCHEMA_STATEMENTS)} schema constraints/indexes")
