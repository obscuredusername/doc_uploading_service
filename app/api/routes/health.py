import logging

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness — always 200 as long as the process is up."""
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}


@router.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "reachable"}


@router.get("/ready")
async def ready(response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Readiness — only 200 when downstream dependencies are reachable.

    Used by container orchestrators to gate traffic. Returns 503 with a
    `checks` map identifying which dependency failed.
    """
    checks: dict[str, str] = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready: db check failed: %s", exc)
        checks["db"] = f"fail: {exc.__class__.__name__}"

    try:
        client = redis_asyncio.from_url(settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready: redis check failed: %s", exc)
        checks["redis"] = f"fail: {exc.__class__.__name__}"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
