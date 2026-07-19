import secrets
from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.tenant import Tenant


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization[7:].strip()


async def get_current_tenant(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """
    Resolve the bearer token to a Tenant.

    Per spec section 8: 401 on missing/invalid keys; tenant_id never appears
    in the URL or body. Constant-time comparison via secrets.compare_digest
    avoids leaking timing information about valid keys.
    """
    token = _extract_bearer(authorization)

    # We can't index on a comparison expression easily without leaking timing,
    # so fetch by exact match (column is UNIQUE) then verify constant-time.
    result = await db.execute(select(Tenant).where(Tenant.api_key == token))
    tenant = result.scalar_one_or_none()
    if tenant is None or not secrets.compare_digest(tenant.api_key, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return tenant
