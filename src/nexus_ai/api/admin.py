"""Admin API — trigger collectors and check status."""

import asyncio
import time
from fastapi import APIRouter, Depends

from nexus_ai.api.auth import require_api_key

router = APIRouter(prefix="/admin", dependencies=[Depends(require_api_key)])

# Track last run status
_collector_status: dict[str, dict] = {}

COLLECTORS = {
    "swagger": "nexus_ai.collectors.swagger",
    "confluence": "nexus_ai.collectors.confluence",
    "postman_bruno": "nexus_ai.collectors.postman_bruno",
    "db_schema": "nexus_ai.collectors.db_schema",
    "service_catalog": "nexus_ai.collectors.service_catalog",
    "dependency_mapper": "nexus_ai.collectors.dependency_mapper",
}


@router.get("/collectors/status")
async def get_collector_status():
    """Show last run time and status for each collector."""
    return {
        "collectors": _collector_status,
        "available": list(COLLECTORS.keys()),
    }


@router.post("/collectors/{name}/run")
async def run_collector(name: str):
    """Manually trigger a collector to re-index data.

    Available collectors: swagger, confluence, postman_bruno, db_schema, service_catalog, dependency_mapper
    """
    if name not in COLLECTORS:
        return {"error": f"Unknown collector '{name}'", "available": list(COLLECTORS.keys())}

    module_path = COLLECTORS[name]
    _collector_status[name] = {"status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}

    try:
        # Import and run the collector's main() function
        import importlib
        module = importlib.import_module(module_path)
        main_fn = getattr(module, "main", None) or getattr(module, "run", None)

        if main_fn is None:
            return {"error": f"Collector '{name}' has no main() or run() function"}

        if asyncio.iscoroutinefunction(main_fn):
            await main_fn()
        else:
            main_fn()

        _collector_status[name] = {
            "status": "success",
            "started_at": _collector_status[name]["started_at"],
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return {"status": "success", "collector": name, "message": f"{name} collector completed."}

    except Exception as e:
        _collector_status[name] = {
            "status": "failed",
            "started_at": _collector_status[name]["started_at"],
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "error": str(e),
        }
        return {"status": "failed", "collector": name, "error": str(e)}


@router.post("/collectors/run-all")
async def run_all_collectors():
    """Run all collectors sequentially. Use for initial setup or full refresh."""
    results = {}
    for name in COLLECTORS:
        resp = await run_collector(name)
        results[name] = resp.get("status", "unknown")
    return {"results": results}


@router.post("/embeddings/backfill")
async def backfill_embeddings_endpoint():
    """Generate embeddings for all chunks that don't have one yet.

    Incremental — only processes rows where embedding IS NULL.
    Safe to re-run (skips already-embedded chunks).
    Cost: ~$0.02 for 1,757 chunks. $0 if all already embedded.
    """
    from nexus_ai.tools.embeddings import backfill_embeddings
    stats = await backfill_embeddings()
    return {"status": "success", **stats}
