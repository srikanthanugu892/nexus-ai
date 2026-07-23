"""Chat API endpoint — receives questions and returns agent responses."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends

from nexus_ai.api.auth import require_api_key
from nexus_ai.agent.orchestrator import run_agent
from nexus_ai.config import settings

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


class ChatRequest(BaseModel):
    """Chat request body."""
    message: str
    conversation_history: list[dict] | None = None
    include_evidence: bool = True


class ChatResponse(BaseModel):
    """Chat response body."""
    answer: str
    tool_calls: list[dict] | None = None
    model: str
    total_tokens: int
    duration_ms: int


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Ask Nexus AI a question about the engineering ecosystem.

    The agent will plan and execute tool calls to answer your question,
    then provide a reasoned response with evidence.
    """
    try:
        result = await run_agent(
            question=request.message,
            conversation_history=request.conversation_history,
        )
    except Exception as e:
        error_msg = str(e)
        if "Connection error" in error_msg or "APIConnectionError" in error_msg:
            return ChatResponse(
                answer="LLM service is unreachable. Check LITELLM_ENDPOINT and LITELLM_API_KEY in .env configuration.",
                tool_calls=[],
                model=settings.llm_model,
                total_tokens=0,
                duration_ms=0,
            )
        raise

    return ChatResponse(
        answer=result["answer"],
        tool_calls=result["tool_calls"] if request.include_evidence else None,
        model=result["model"],
        total_tokens=result["total_tokens"],
        duration_ms=result["duration_ms"],
    )
