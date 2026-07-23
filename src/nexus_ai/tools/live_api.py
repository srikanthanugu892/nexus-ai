"""Live API tools — call internal service endpoints at question-time.

These tools let the agent query live services for runtime state that isn't
captured in pre-indexed data (current configs, live status, counts, etc.)

Supports automatic OAuth2 authentication via Vault → OAuth provider for services that require it.

CONFIGURATION:
  - Set ALLOWED_SERVICE_HOSTS in your .env or data/service_hosts.json to restrict which
    hosts the agent can call (security allowlist).
  - OAuth2 client credentials are fetched from Vault at runtime (never stored locally).
"""

import json
import os
import time as _time
from urllib.parse import urlparse

import httpx

from nexus_ai.config import settings

# TLS verification setting from config
_TLS_VERIFY = settings.tls_verify

# Allowed service hostname patterns (security: only call known internal services)
# Configure via ALLOWED_SERVICE_HOSTS env var (comma-separated) or data/allowed_hosts.json
_DEFAULT_ALLOWED_HOSTS = [
    # Add your internal service hostnames here
    # e.g., "api.internal.example.com",
    # e.g., "*.staging.example.com",
]


def _load_allowed_hosts() -> list[str]:
    """Load allowed hosts from environment or config file."""
    env_hosts = os.environ.get("ALLOWED_SERVICE_HOSTS", "")
    if env_hosts:
        return [h.strip() for h in env_hosts.split(",") if h.strip()]

    # Try loading from config file
    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent.parent / "data" / "allowed_hosts.json"
    if config_path.exists():
        with open(config_path) as f:
            data = json.load(f)
            return data.get("hosts", [])

    return _DEFAULT_ALLOWED_HOSTS


ALLOWED_HOSTS = _load_allowed_hosts()

# Token cache — stores (token, expiry_timestamp)
_oauth_tokens: dict[str, tuple[str, float]] = {}

# Request timeout
TIMEOUT = 15.0


def _get_cached_token(service_host: str) -> str | None:
    """Return cached token if not expired (with 60s buffer)."""
    if service_host in _oauth_tokens:
        token, expires_at = _oauth_tokens[service_host]
        if _time.time() < expires_at - 60:
            return token
        del _oauth_tokens[service_host]
    return None


def _cache_token(service_host: str, token: str, expires_in: int = 86400) -> None:
    """Cache a token with its expiry time."""
    _oauth_tokens[service_host] = (token, _time.time() + expires_in)


def _is_allowed_url(url: str) -> bool:
    """Security check: only allow calls to configured internal services."""
    if not ALLOWED_HOSTS:
        return False  # Deny all if no hosts configured

    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    for allowed in ALLOWED_HOSTS:
        if allowed.startswith("*."):
            # Wildcard match: *.example.com matches foo.example.com
            suffix = allowed[2:]
            if hostname.endswith(suffix):
                return True
        elif hostname == allowed:
            return True
    return False


async def _get_oauth_token(service_host: str) -> str | None:
    """Get OAuth2 token for a service via client_credentials flow.

    Fetches client_secret from Vault, then exchanges for a bearer token.
    """
    cached = _get_cached_token(service_host)
    if cached:
        return cached

    vault_host = settings.vault_host
    vault_token = settings.vault_token

    if not vault_token:
        # Try reading from ~/.vault-token
        import os
        for path in [os.path.expanduser("~/.vault-token")]:
            try:
                with open(path) as f:
                    vault_token = f.read().strip()
                    if vault_token:
                        break
            except (FileNotFoundError, PermissionError):
                pass

    if not vault_token:
        return None

    try:
        async with httpx.AsyncClient(verify=_TLS_VERIFY, timeout=10.0) as client:
            # Fetch OAuth2 credentials from Vault
            resp = await client.get(
                f"https://{vault_host}/v1/secret/data/oauth2",
                headers={"X-Vault-Token": vault_token},
            )
            if resp.status_code != 200:
                return None

            vault_data = resp.json().get("data", {}).get("data", {})
            client_secret = vault_data.get("client_secret", "")
            client_id = vault_data.get("client_id", "") or settings.auth0_client_id

            if not client_secret or not client_id:
                return None

            # Get OAuth2 token
            auth0_issuer = settings.auth0_issuer.rstrip("/")
            token_resp = await client.post(
                f"{auth0_issuer}/oauth/token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": f"https://{service_host}",
                    "grant_type": "client_credentials",
                },
            )
            if token_resp.status_code == 200:
                token_data = token_resp.json()
                token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 86400)
                if token:
                    _cache_token(service_host, token, expires_in)
                    return token
    except Exception:
        pass

    return None


async def call_service_api(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> str:
    """Call a live internal service API endpoint.

    Security: Only allows calls to hosts in the configured allowlist.
    Automatically handles OAuth2 authentication on 401 responses.

    Args:
        url: Full URL to call (must be an allowed internal service)
        method: HTTP method (GET, POST). Defaults to GET.
        body: Optional JSON body for POST requests.
        headers: Optional custom headers (e.g., {"X-Tenant-Id": "default"})
    """
    if not _is_allowed_url(url):
        return json.dumps({
            "error": "URL not allowed. Only calls to configured internal services are permitted.",
            "hint": "Configure ALLOWED_SERVICE_HOSTS in .env or data/allowed_hosts.json",
        })

    method = method.upper()
    if method not in ("GET", "POST"):
        return json.dumps({"error": "Only GET and POST methods are allowed."})

    request_headers = {}
    if headers:
        request_headers.update(headers)

    try:
        async with httpx.AsyncClient(verify=_TLS_VERIFY, timeout=TIMEOUT) as client:
            if method == "GET":
                resp = await client.get(url, headers=request_headers)
            else:
                resp = await client.post(url, json=body or {}, headers=request_headers)

            # Auto-retry with OAuth2 if we get 401
            if resp.status_code == 401 and "Authorization" not in request_headers:
                parsed = urlparse(url)
                if parsed.hostname in _oauth_tokens:
                    del _oauth_tokens[parsed.hostname]
                token = await _get_oauth_token(parsed.hostname)
                if token:
                    request_headers["Authorization"] = f"Bearer {token}"
                    if method == "GET":
                        resp = await client.get(url, headers=request_headers)
                    else:
                        resp = await client.post(url, json=body or {}, headers=request_headers)

            # Parse response
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:2000]

            # Truncate large responses
            response_str = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
            if len(response_str) > 5000:
                if isinstance(data, list):
                    return json.dumps({
                        "status_code": resp.status_code,
                        "data": data[:10],
                        "truncated": True,
                        "total_items": len(data),
                    })
                elif isinstance(data, dict):
                    return json.dumps({
                        "status_code": resp.status_code,
                        "data": response_str[:5000],
                        "truncated": True,
                    })

            return json.dumps({"status_code": resp.status_code, "data": data})

    except httpx.TimeoutException:
        return json.dumps({"error": f"Request timed out after {TIMEOUT}s", "url": url})
    except httpx.ConnectError as e:
        return json.dumps({"error": f"Connection failed: {str(e)}", "url": url})
    except Exception as e:
        return json.dumps({"error": f"Request failed: {type(e).__name__}: {str(e)}"})
