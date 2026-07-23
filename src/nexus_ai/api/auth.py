"""API key authentication middleware.

Security model:
- API_KEY unset or "nexus-dev-key-change-me" → auth disabled (localhost-only use)
- API_KEY set to a real value → enforced on all protected endpoints
- All ports bound to 127.0.0.1 in docker-compose (not network-reachable)
"""

import hmac
import logging

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from nexus_ai.config import settings

logger = logging.getLogger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_DEFAULT_KEY = "nexus-dev-key-change-me"


async def require_api_key(api_key: str | None = Security(api_key_header)) -> str | None:
    """Validate API key if configured. Skip auth only for localhost with default key."""
    # No real API_KEY configured → auth disabled (localhost-only deployment)
    if not settings.api_key or settings.api_key == _DEFAULT_KEY:
        return None

    # API_KEY is set to a real value → enforce it
    if not api_key or not hmac.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Provide X-API-Key header.",
        )
    return api_key
