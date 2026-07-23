"""MCP HTTP tools — direct HTTP-based MCP server queries.

Placeholder for custom MCP server integrations that use streamable HTTP transport.
"""

import json

import httpx

from nexus_ai.config import settings


async def query_mcp_http(query: str) -> str:
    """Query an HTTP-based MCP server directly (e.g., for config analysis).

    This is a fallback for MCP servers that aren't managed by the MCP client manager.
    Configure the MCP URL in your .env as MCP_HTTP_URL.
    """
    import os
    mcp_url = os.environ.get("MCP_HTTP_URL", "")

    if not mcp_url:
        return json.dumps({"error": "No HTTP MCP server configured. Set MCP_HTTP_URL in .env"})

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                mcp_url,
                json={"query": query},
                headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
            )
            if resp.status_code == 200:
                return json.dumps(resp.json())
            return json.dumps({"error": f"MCP HTTP error: {resp.status_code}", "body": resp.text[:500]})
    except Exception as e:
        return json.dumps({"error": f"MCP HTTP query failed: {str(e)}"})
