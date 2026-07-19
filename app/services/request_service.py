"""
Collection-portal service layer.

Mints one-time upload links (one per doc type) for a client reference, and
handles the client-side upload that spends a link. Files land on the local
storage backend (spec decision #5). The third-party lookup is stubbed for
now (Phase 2 seam — details TBD).
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import doc_types
from app.models.document import Document
from app.models.document_request import DocumentRequest, DocumentRequestStatus
from app.models.tenant import Tenant
from app.services.document_service import _validate_upload
from app.services.storage import get_storage_backend

_TOKEN_BYTES = 32


def build_link_url(reference: str, doc_type: str, token: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/u/{reference}/{doc_type}/{token}"


async def get_portal_tenant(db: AsyncSession) -> Tenant:
    """Resolve the tenant the staff portal operates as.

    Uses ``PORTAL_TENANT_NAME`` if set; otherwise falls back to the single
    tenant when exactly one exists. Raises a clear 500 if ambiguous/missing
    so misconfiguration is visible rather than silent.
    """
    if settings.portal_tenant_name:
        tenant = (
            await db.execute(
                select(Tenant).where(Tenant.name == settings.portal_tenant_name)
            )
        ).scalar_one_or_none()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Portal tenant {settings.portal_tenant_name!r} not found",
            )
        return tenant

    tenants = (await db.execute(select(Tenant).limit(2))).scalars().all()
    if len(tenants) == 1:
        return tenants[0]
    if not tenants:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No tenant configured. Run scripts/create_tenant.py first.",
        )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Multiple tenants exist; set PORTAL_TENANT_NAME to pick one.",
    )


async def create_request_batch(
    db: AsyncSession, *, tenant: Tenant, name: str, reference: str
) -> tuple[uuid.UUID, list[DocumentRequest]]:
    """Mint one upload link per doc type. Returns (batch_id, requests)."""
    batch_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.document_request_ttl_days
    )

    requests: list[DocumentRequest] = []
    for slug in doc_types.all_slugs():
        req = DocumentRequest(
            tenant_id=tenant.id,
            batch_id=batch_id,
            reference=reference,
            client_name=name,
            doc_type=slug,
            token=secrets.token_urlsafe(_TOKEN_BYTES),
            status=DocumentRequestStatus.PENDING,
            expires_at=expires_at,
        )
        db.add(req)
        requests.append(req)

    await db.commit()
    for req in requests:
        await db.refresh(req)
    return batch_id, requests


async def get_by_token(
    db: AsyncSession, *, reference: str, doc_type: str, token: str
) -> DocumentRequest | None:
    stmt = select(DocumentRequest).where(
        DocumentRequest.reference == reference,
        DocumentRequest.doc_type == doc_type,
        DocumentRequest.token == token,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def submit_upload(
    db: AsyncSession,
    *,
    req: DocumentRequest,
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
) -> Document:
    """Store an uploaded file locally, create/refresh its Document, and spend
    the link (mark submitted). Raises HTTPException on validation failure."""
    _validate_upload(file_bytes, mime_type)

    tenant_id = req.tenant_id
    backend = get_storage_backend("local")
    key = f"{tenant_id}/{req.reference}/{req.id}/original__{file_name}"
    backend.save(key, file_bytes, content_type=mime_type)
    public_url = backend.public_url(key)

    # Respect the Document dedup key: overwrite if the same logical doc exists.
    existing = (
        await db.execute(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.owner_ref == req.reference,
                Document.file_name == file_name,
                Document.document_type == req.doc_type,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.mime_type = mime_type
        existing.file_size = len(file_bytes)
        existing.s3_url = public_url
        existing.uploaded_by = req.client_name or "portal"
        doc = existing
    else:
        doc = Document(
            tenant_id=tenant_id,
            owner_ref=req.reference,
            file_name=file_name,
            document_type=req.doc_type,
            original_document_type=req.doc_type,
            mime_type=mime_type,
            file_size=len(file_bytes),
            s3_url=public_url,
            uploaded_by=req.client_name or "portal",
        )
        db.add(doc)

    await db.flush()

    req.status = DocumentRequestStatus.SUBMITTED
    req.submitted_at = datetime.now(timezone.utc)
    req.document_id = doc.id

    await db.commit()
    await db.refresh(doc)
    return doc
