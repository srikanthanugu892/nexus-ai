"""Tool registry — maps tool names to implementations and provides OpenAI-format schemas."""

from nexus_ai.tools.graph_tools import find_service, find_owner, list_team_services, find_api_consumers
from nexus_ai.tools.search_tools import search_documentation
from nexus_ai.tools.live_api import call_service_api
from nexus_ai.tools.jira_tools import find_incidents, search_jira
from nexus_ai.tools.impact_tools import calculate_impact
from nexus_ai.tools.db_tools import find_database_info, query_database

# Local tool implementations (graph queries, vector search, live API, Jira)
# MCP tools are dynamically loaded from data/mcp.json when enabled
TOOL_IMPLEMENTATIONS = {
    "find_service": find_service,
    "find_owner": find_owner,
    "list_team_services": list_team_services,
    "find_api_consumers": find_api_consumers,
    "search_documentation": search_documentation,
    "call_service_api": call_service_api,
    "find_incidents": find_incidents,
    "search_jira": search_jira,
    "calculate_impact": calculate_impact,
    "find_database_info": find_database_info,
    "query_database": query_database,
}

# OpenAI-format tool definitions for LLM tool-use
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "find_service",
            "description": "Find a service by name (fuzzy match). Returns details: language, owner, swagger URL, repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Service name or partial name (e.g., 'payments', 'auth', 'orders')"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_owner",
            "description": "Find which team owns a service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Service name"}
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_team_services",
            "description": "List all services owned by a team.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {"type": "string", "description": "Team name"}
                },
                "required": ["team_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_api_consumers",
            "description": "Find upstream services that call/depend on a given service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Service to find consumers of"}
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_documentation",
            "description": "Search indexed docs, API specs, Postman collections, and Confluence pages via semantic + text search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (e.g., 'payment processing flow', 'user authentication endpoint')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "call_service_api",
            "description": "Call a live internal service API for runtime state. Auto-handles OAuth2 on 401.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL of the internal service endpoint"},
                    "method": {"type": "string", "enum": ["GET", "POST"], "description": "HTTP method. Default: GET."},
                    "body": {"type": "object", "description": "JSON body for POST requests."},
                    "headers": {"type": "object", "description": "Custom headers (e.g., {\"X-Tenant-Id\": \"default\"})"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_incidents",
            "description": "Find recent bugs/incidents for a service via live Jira query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Service name"}
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_jira",
            "description": "Search Jira tickets by keyword. Returns recent matching tickets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (e.g., 'payment failure', 'login timeout')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_impact",
            "description": "Assess risk of changing/removing a service or endpoint. Traces consumers, scores risk (HIGH/MEDIUM/LOW).",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Service being changed"},
                    "change_description": {"type": "string", "description": "What's changing (e.g., 'remove /users endpoint')"}
                },
                "required": ["service", "change_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_database_info",
            "description": "Search DB schemas across all platform databases for tables/columns matching a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Table name, column, or concept (e.g., 'user profile', 'order status')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "Run read-only SELECT against a live database via Vault dynamic credentials.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name (e.g., 'orders', 'users', 'payments')"},
                    "sql": {"type": "string", "description": "SELECT query (e.g., 'SELECT count(*) FROM orders')"},
                    "environment": {"type": "string", "description": "Environment: dev (default), staging, production"}
                },
                "required": ["database", "sql"]
            }
        }
    },
]
