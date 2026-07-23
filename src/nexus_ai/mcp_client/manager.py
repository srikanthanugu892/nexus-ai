"""MCP Client Manager — connects to MCP servers using the official Python SDK.

Supports both transport types:
- Streamable HTTP: Client("http://...") or Client(streamable_http_client(url, http_client=...))
- Stdio: Client(stdio_client(StdioServerParameters(...)))

Reference: https://py.sdk.modelcontextprotocol.io/v2/client/
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from nexus_ai.config import settings

# Config path
_docker_path = Path("/app/data/mcp.json")
_local_path = Path(__file__).parent.parent.parent.parent / "data" / "mcp.json"
MCP_CONFIG_PATH = _docker_path if _docker_path.exists() else _local_path


def _resolve_env_vars(env_config: dict) -> dict:
    """Resolve ${VAR} references in env config."""
    var_map = {
        "ATLASSIAN_URL": settings.atlassian_url,
        "ATLASSIAN_EMAIL": settings.atlassian_email,
        "ATLASSIAN_API_TOKEN": settings.atlassian_api_token,
        "GITHUB_TOKEN": settings.github_token,
        "GITHUB_HOST": settings.github_host,
        "OPENAI_API_KEY": settings.litellm_api_key,
        "LITELLM_API_KEY": settings.litellm_api_key,
    }

    resolved = {}
    for key, value in env_config.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            var_name = value[2:-1]
            resolved[key] = var_map.get(var_name, os.environ.get(var_name, ""))
        else:
            resolved[key] = value
    return resolved


class MCPClientManager:
    """Manages connections to multiple MCP servers using the official SDK."""

    def __init__(self):
        self._clients: dict[str, Client] = {}
        self._tools_cache: dict[str, list[dict]] = {}
        self._tool_to_server: dict[str, str] = {}
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Load config and connect to all enabled MCP servers."""
        async with self._lock:
            if self._initialized:
                return

            if not MCP_CONFIG_PATH.exists():
                print("⚠ No mcp.json found — MCP tools will not be available")
                self._initialized = True
                return

            with open(MCP_CONFIG_PATH) as f:
                config = json.load(f)

            servers = config.get("mcpServers", {})
            print(f"\nMCP: Connecting to {len(servers)} servers...")

            for name, server_config in servers.items():
                if server_config.get("disabled", False):
                    print(f"  ⊘ {name}: disabled")
                    continue

                try:
                    await self._connect_server(name, server_config)
                    tool_count = len(self._tools_cache.get(name, []))
                    print(f"  ✓ {name}: connected ({tool_count} tools)")
                except Exception as e:
                    print(f"  ✗ {name}: {type(e).__name__}: {e}")

            self._initialized = True
            total = len(self._tool_to_server)
            print(f"MCP: {total} tools available from {len(self._clients)} servers\n")

    async def _connect_server(self, name: str, config: dict):
        """Connect to a single MCP server based on its transport type."""
        server_type = config.get("type", "stdio")  # Default to stdio if no type specified
        env_config = _resolve_env_vars(config.get("env", {}))
        headers = config.get("headers", {})
        # Resolve header vars too
        resolved_headers = {}
        for k, v in headers.items():
            if isinstance(v, str) and "${" in v:
                var_name = v.replace("${", "").replace("}", "").replace("Bearer ", "")
                if v.startswith("Bearer "):
                    resolved_headers[k] = f"Bearer {_resolve_env_vars({k: '${' + var_name + '}'})[k]}"
                else:
                    resolved_headers[k] = _resolve_env_vars({k: v})[k]
            else:
                resolved_headers[k] = v

        if server_type in ("http", "streamableHttp"):
            # HTTP-based MCP — use streamable_http_client with httpx
            url = config["url"]
            http_client = httpx.AsyncClient(
                headers=resolved_headers,
                timeout=httpx.Timeout(30.0, read=300.0),
                follow_redirects=True,
            )
            transport = streamable_http_client(url, http_client=http_client)
            client = Client(transport)

        elif config.get("command") == "docker":
            # Docker stdio — spawn docker run -i
            server_params = StdioServerParameters(
                command="docker",
                args=config.get("args", []),
                env=env_config,
            )
            client = Client(stdio_client(server_params))

        elif config.get("command") in ("uvx", "npx", "node", "python"):
            # Generic stdio subprocess
            server_params = StdioServerParameters(
                command=config["command"],
                args=config.get("args", []),
                env=env_config,
            )
            client = Client(stdio_client(server_params))

        else:
            raise ValueError(f"Unknown MCP server type/command: {server_type}/{config.get('command')}")

        # Enter the client context (connects and negotiates)
        await client.__aenter__()

        try:
            # Discover tools
            tools_response = await client.list_tools()
            tools = []
            for tool in tools_response.tools:
                tool_def = {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema if tool.input_schema else {"type": "object", "properties": {}},
                }
                tools.append(tool_def)
                self._tool_to_server[tool.name] = name

            self._tools_cache[name] = tools
            self._clients[name] = client
        except Exception:
            # Clean up the entered context if tool discovery fails
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
            raise

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on the appropriate MCP server."""
        server_name = self._tool_to_server.get(tool_name)
        if not server_name:
            return json.dumps({"error": f"MCP tool '{tool_name}' not found"})

        client = self._clients.get(server_name)
        if not client:
            return json.dumps({"error": f"MCP server '{server_name}' not connected"})

        try:
            result = await client.call_tool(tool_name, arguments)
            # Extract text content
            if result.content:
                texts = [c.text for c in result.content if hasattr(c, 'text')]
                return "\n".join(texts) if texts else json.dumps({"result": "empty"})
            return json.dumps({"result": "no content"})
        except Exception as e:
            return json.dumps({"error": f"MCP call failed: {type(e).__name__}: {str(e)}"})

    def get_openai_tool_definitions(self) -> list[dict]:
        """Get OpenAI-format tool definitions for all MCP tools."""
        definitions = []
        for server_name, tools in self._tools_cache.items():
            for tool in tools:
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    }
                })
        return definitions

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_to_server

    def get_server_tool_summary(self) -> dict[str, list[str]]:
        """Return a summary of tools per server (for meta-tool description)."""
        return {name: [t["name"] for t in tools] for name, tools in self._tools_cache.items()}

    def get_all_tool_names(self) -> list[str]:
        return list(self._tool_to_server.keys())

    async def close(self):
        """Close all MCP client connections."""
        for name, client in self._clients.items():
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
        self._clients.clear()
        self._tool_to_server.clear()
        self._tools_cache.clear()
        self._initialized = False


# Singleton
_manager: MCPClientManager | None = None


async def get_mcp_manager() -> MCPClientManager:
    """Get or create the MCP client manager singleton."""
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    if not _manager._initialized:
        await _manager.initialize()
    return _manager


async def close_mcp():
    """Shutdown MCP manager."""
    global _manager
    if _manager:
        await _manager.close()
        _manager = None
