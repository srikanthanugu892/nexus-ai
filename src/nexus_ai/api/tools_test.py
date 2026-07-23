"""Direct tool testing endpoint — bypass LLM, call tools directly for verification."""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from nexus_ai.api.auth import require_api_key
from nexus_ai.tools.registry import TOOL_IMPLEMENTATIONS

router = APIRouter(prefix="/api/tools", dependencies=[Depends(require_api_key)])


class ToolTestRequest(BaseModel):
    """Direct tool call request."""
    tool_name: str
    arguments: dict


@router.post("/test")
async def test_tool(request: ToolTestRequest):
    """Call a tool directly for testing (bypasses LLM)."""
    tool_fn = TOOL_IMPLEMENTATIONS.get(request.tool_name)
    if not tool_fn:
        return {"error": f"Unknown tool: {request.tool_name}", "available": list(TOOL_IMPLEMENTATIONS.keys())}

    try:
        result = await tool_fn(**request.arguments)
        return {"tool": request.tool_name, "result": json.loads(result)}
    except Exception as e:
        return {"tool": request.tool_name, "error": str(e)}
