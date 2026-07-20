"""
Collection-portal service layer.

Mints one-time upload links (one per doc type) for a client reference, and
handles the client-side upload that spends a link. Files land on the local
storage backend (spec decision #5). The third-party lookup is stubbed for
now (Phase 2 seam — details TBD).
"""
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import doc_types
from app.models.document import Document, OcrStatus
from app.models.document_request import DocumentRequest, DocumentRequestStatus
from app.models.tenant import Tenant
from app.services.document_service import _validate_upload
from app.services.ocr import extract_flat_text, perform_ocr
from app.services.storage import get_storage_backend, keys

logger = logging.getLogger(__name__)

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

    # Pre-create the case folder with one empty subfolder per doc type, so the
    # 27-folder layout exists on disk as soon as the case is initiated.
    backend = get_storage_backend("local")
    case = keys.case_folder(reference, name)
    for slug in doc_types.all_slugs():
        backend.ensure_dir(f"{case}/{slug}")

    return batch_id, requests


async def client_name_for_reference(
    db: AsyncSession, *, tenant: Tenant, reference: str
) -> str | None:
    """The client name recorded for a case (from its generated links), if any."""
    return (
        await db.execute(
            select(DocumentRequest.client_name)
            .where(
                DocumentRequest.tenant_id == tenant.id,
                DocumentRequest.reference == reference,
                DocumentRequest.client_name.isnot(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def store_local_document(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    reference: str,
    client_name: str | None,
    doc_type: str,
    uploaded_by: str,
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
    run_ocr: bool,
    metadata: dict | None = None,
) -> Document:
    """Store a file under <reference>__<name>/<doc_type>/, optionally OCR it
    (storing a JSON sidecar next to it), and upsert the Document row.

    OCR runs synchronously via the OCR.space provider. OCR failure does NOT
    fail the upload — the doc is stored with ocr_status='failed' + the error.
    """
    _validate_upload(file_bytes, mime_type)

    backend = get_storage_backend("local")
    dkey = keys.doc_key(reference, client_name, doc_type, file_name)
    stored_name = keys.sanitize_filename(file_name)
    backend.save(dkey, file_bytes, content_type=mime_type)
    file_url = backend.public_url(dkey)

    ocr_status = OcrStatus.NOT_REQUESTED
    ocr_text: str | None = None
    ocr_error: str | None = None
    json_url: str | None = None

    if run_ocr:
        try:
            is_table = doc_type in settings.ocr_table_mode_document_types
            payload = await run_in_threadpool(
                perform_ocr,
                file_bytes,
                mime_type,
                is_table=is_table,
                file_name=stored_name,
            )
            ocr_text = extract_flat_text(payload)
            jkey = keys.json_key(reference, client_name, doc_type, file_name)
            backend.save(
                jkey,
                json.dumps(payload).encode("utf-8"),
                content_type="application/json",
            )
            json_url = backend.public_url(jkey)
            ocr_status = OcrStatus.DONE
        except Exception as exc:  # noqa: BLE001 — OCR failure must not lose the file
            logger.warning("OCR failed for %s/%s: %s", reference, doc_type, exc)
            ocr_status = OcrStatus.FAILED
            ocr_error = str(exc)[:500]

    existing = (
        await db.execute(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.owner_ref == reference,
                Document.file_name == stored_name,
                Document.document_type == doc_type,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        doc = existing
        doc.mime_type = mime_type
        doc.file_size = len(file_bytes)
        doc.s3_url = file_url
        doc.s3_url_ocr_json = json_url
        doc.uploaded_by = uploaded_by
        doc.ocr_status = ocr_status
        doc.ocr_text = ocr_text
        doc.ocr_error = ocr_error
        if metadata is not None:
            doc.metadata_ = metadata
    else:
        doc = Document(
            tenant_id=tenant_id,
            owner_ref=reference,
            file_name=stored_name,
            document_type=doc_type,
            original_document_type=doc_type,
            mime_type=mime_type,
            file_size=len(file_bytes),
            s3_url=file_url,
            s3_url_ocr_json=json_url,
            uploaded_by=uploaded_by,
            ocr_status=ocr_status,
            ocr_text=ocr_text,
            ocr_error=ocr_error,
            metadata_=metadata or {},
        )
        db.add(doc)

    await db.flush()
    return doc


async def list_requests_for_reference(
    db: AsyncSession, *, tenant: Tenant, reference: str
) -> tuple[list[DocumentRequest], dict]:
    """Return (requests, {document_id: Document}) for a case reference."""
    reqs = (
        await db.execute(
            select(DocumentRequest)
            .where(
                DocumentRequest.tenant_id == tenant.id,
                DocumentRequest.reference == reference,
            )
            .order_by(DocumentRequest.created_at.desc())
        )
    ).scalars().all()

    doc_ids = [r.document_id for r in reqs if r.document_id]
    docs: dict = {}
    if doc_ids:
        rows = (
            await db.execute(select(Document).where(Document.id.in_(doc_ids)))
        ).scalars().all()
        docs = {d.id: d for d in rows}
    return list(reqs), docs


def effective_status(req: DocumentRequest) -> str:
    """Stored status, but a lapsed PENDING link reads as expired."""
    if req.status == DocumentRequestStatus.PENDING and not req.is_live:
        return DocumentRequestStatus.EXPIRED
    return req.status


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
    run_ocr: bool = False,
) -> Document:
    """Store a client upload under the case/doc-type folder, create/refresh its
    Document, and spend the link (mark submitted)."""
    doc = await store_local_document(
        db,
        tenant_id=req.tenant_id,
        reference=req.reference,
        client_name=req.client_name,
        doc_type=req.doc_type,
        uploaded_by=req.client_name or "portal",
        file_bytes=file_bytes,
        file_name=file_name,
        mime_type=mime_type,
        run_ocr=run_ocr,
    )

    req.status = DocumentRequestStatus.SUBMITTED
    req.submitted_at = datetime.now(timezone.utc)
    req.document_id = doc.id

    await db.commit()
    await db.refresh(doc)
    return doc
