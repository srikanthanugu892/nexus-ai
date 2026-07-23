"""Nexus AI — Enterprise Intelligence Agent.

FastAPI application entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from nexus_ai.api.auth import require_api_key
from nexus_ai.api.health import router as health_router
from nexus_ai.api.chat import router as chat_router
from nexus_ai.api.tools_test import router as tools_test_router
from nexus_ai.api.admin import router as admin_router
from nexus_ai.db.neo4j import close_neo4j
from nexus_ai.db.postgres import close_pg
from nexus_ai.mcp_client.manager import close_mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle management."""
    # Startup: connections are lazy-initialized on first use
    yield
    # Shutdown: close all connections
    await close_mcp()
    await close_neo4j()
    await close_pg()


app = FastAPI(
    title="Nexus AI",
    description="Enterprise Intelligence Agent — ask the enterprise anything",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the React frontend (port 3000) to call the API (port 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health endpoint is public (no auth required for monitoring)
app.include_router(health_router, tags=["health"])

# Chat endpoint (requires API key)
app.include_router(chat_router, tags=["chat"])

# Direct tool testing (requires API key)
app.include_router(tools_test_router, tags=["tools"])

# Admin endpoints — trigger collectors, check status (requires API key)
app.include_router(admin_router, tags=["admin"])


# Protected endpoint placeholder
@app.get("/api/status", dependencies=[Depends(require_api_key)])
async def protected_status():
    """Protected endpoint demonstrating API key auth."""
    return {"message": "Authenticated. Nexus AI is ready."}
