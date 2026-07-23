"""Jira live query tools — query tickets at question-time via REST API.

These are LIVE tools (not pre-indexed). Every call hits the Jira API for fresh data.
Supports Jira Cloud (REST API v3) and Jira Server/Data Center.
"""

import json

import httpx

from nexus_ai.config import settings

JIRA_SEARCH = f"{settings.atlassian_url}/rest/api/3/search/jql"
TIMEOUT = 15.0


def _get_auth() -> tuple[str, str]:
    return (settings.atlassian_email, settings.atlassian_api_token)


def _escape_jql(value: str) -> str:
    """Escape special characters for JQL text search."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_issue(issue: dict) -> dict:
    """Extract key fields from a Jira issue response."""
    fields = issue.get("fields", {})
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary"),
        "status": fields.get("status", {}).get("name") if fields.get("status") else None,
        "priority": fields.get("priority", {}).get("name") if fields.get("priority") else None,
        "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else "Unassigned",
        "issue_type": fields.get("issuetype", {}).get("name") if fields.get("issuetype") else None,
        "created": (fields.get("created") or "")[:10],
        "updated": (fields.get("updated") or "")[:10],
        "url": f"{settings.atlassian_url}/browse/{issue.get('key')}",
    }


async def _run_jql(jql: str, max_results: int = 10) -> dict:
    """Execute a JQL query against the Jira search API."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            JIRA_SEARCH,
            params={
                "jql": jql,
                "maxResults": max_results,
                "fields": "summary,status,priority,assignee,issuetype,created,updated",
            },
            auth=_get_auth(),
        )
        resp.raise_for_status()
        return resp.json()


async def find_incidents(service_name: str) -> str:
    """Find recent incidents, bugs, and issues related to a service."""
    if not settings.atlassian_email or not settings.atlassian_api_token:
        return json.dumps({"error": "Jira credentials not configured. Set ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in .env"})

    safe_name = _escape_jql(service_name)
    jql = f'text ~ "{safe_name}" AND issuetype in (Bug, Incident) ORDER BY updated DESC'

    try:
        data = await _run_jql(jql)
        issues = [_format_issue(i) for i in data.get("issues", [])]
        total = data.get("total", len(issues))

        if not issues:
            jql_broad = f'text ~ "{safe_name}" ORDER BY updated DESC'
            data = await _run_jql(jql_broad)
            issues = [_format_issue(i) for i in data.get("issues", [])]
            total = data.get("total", len(issues))

            if not issues:
                return json.dumps({
                    "service": service_name,
                    "issues": [],
                    "note": f"No Jira tickets found mentioning '{service_name}'.",
                })

        return json.dumps({
            "service": service_name,
            "issues": issues,
            "total_matching": total,
            "showing": len(issues),
        })

    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Jira API error: HTTP {e.response.status_code}"})
    except httpx.ConnectError:
        return json.dumps({"error": "Cannot connect to Jira. Check network and ATLASSIAN_URL."})
    except Exception as e:
        return json.dumps({"error": f"Jira query failed: {type(e).__name__}: {str(e)}"})


async def search_jira(query: str) -> str:
    """Search Jira tickets using a natural language query."""
    if not settings.atlassian_email or not settings.atlassian_api_token:
        return json.dumps({"error": "Jira credentials not configured. Set ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in .env"})

    safe_query = _escape_jql(query)
    jql = f'text ~ "{safe_query}" ORDER BY updated DESC'

    try:
        data = await _run_jql(jql)
        issues = [_format_issue(i) for i in data.get("issues", [])]
        total = data.get("total", len(issues))

        if not issues:
            return json.dumps({"query": query, "issues": [], "note": f"No Jira tickets found matching '{query}'."})

        return json.dumps({
            "query": query,
            "issues": issues,
            "total_matching": total,
            "showing": len(issues),
        })

    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Jira API error: HTTP {e.response.status_code}"})
    except Exception as e:
        return json.dumps({"error": f"Jira query failed: {type(e).__name__}: {str(e)}"})
