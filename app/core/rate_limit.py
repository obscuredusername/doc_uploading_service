"""
Per-tenant rate limiting (spec §15.4 — picking sensible defaults).

Key strategy:
  - If the request has a Bearer token, key by that token (one bucket per
    tenant api_key, regardless of source IP).
  - Otherwise key by client IP. This catches login probing.

Storage: the same Redis instance used by Celery. slowapi delegates to the
`limits` library, which speaks to Redis directly via storage_uri.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

DEFAULT_LIMIT = "120/minute"
UPLOAD_LIMIT = "30/minute"


def tenant_or_ip_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return f"tenant:{auth[7:].strip()}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=tenant_or_ip_key,
    default_limits=[DEFAULT_LIMIT],
    storage_uri=settings.rate_limit_storage_uri,
    headers_enabled=True,
)
