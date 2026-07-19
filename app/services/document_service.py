"""
Document service layer.

Encapsulates the create-or-update (dedup), delete-with-cascade, and
ocr-enqueue flows defined in spec §6 and §11. Route handlers stay thin.
"""
import io
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import notifier
from app.models.document import Base64Status, Document, OcrStatus
from app.models.tenant import Tenant
from app.services.s3_service import (
    S3Service,
    build_ocr_json_key,
    build_original_key,
    refresh_document_urls,
)

logger = logging.getLogger(__name__)


def _validate_upload(file_bytes: bytes, mime_type: str) -> None:
    if len(file_bytes) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.upload_max_bytes} bytes",
        )
    if mime_type not in settings.upload_mime_allowlist:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported mime type: {mime_type}",
        )


async def _find_by_dedup_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    owner_ref: str,
    file_name: str,
    document_type: str,
) -> Document | None:
    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.owner_ref == owner_ref,
        Document.file_name == file_name,
        Document.document_type == document_type,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_or_update_document(
    db: AsyncSession,
    *,
    tenant: Tenant,
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
    owner_ref: str,
    document_type: str,
    uploaded_by: str,
    group_tag: uuid.UUID | None,
    metadata: dict[str, Any],
    queue_ocr: bool,
) -> tuple[Document, bool]:
    """Returns (document, was_created)."""
    _validate_upload(file_bytes, mime_type)

    existing = await _find_by_dedup_key(
        db,
        tenant_id=tenant.id,
        owner_ref=owner_ref,
        file_name=file_name,
        document_type=document_type,
    )

    doc_id = existing.id if existing else uuid.uuid4()
    s3_key = build_original_key(tenant.id, owner_ref, doc_id, file_name)

    s3 = S3Service()
    s3.upload_fileobj(io.BytesIO(file_bytes), s3_key, content_type=mime_type)

    ocr_status = OcrStatus.PENDING if queue_ocr else OcrStatus.NOT_REQUESTED

    initial_presigned = s3.generate_presigned_get(
        s3_key, settings.s3_presigned_url_expires_seconds
    )

    if existing:
        # Spec §11 dedup semantics: overwrite mutable fields, freeze
        # original_document_type, replace S3 object at same key.
        existing.document_type = document_type
        existing.mime_type = mime_type
        existing.file_size = len(file_bytes)
        existing.uploaded_by = uploaded_by
        existing.group_tag = group_tag
        existing.metadata_ = metadata
        existing.ocr_status = ocr_status
        existing.ocr_text = None
        existing.ocr_error = None
        existing.base64_status = Base64Status.PENDING
        existing.base64_error = None
        document = existing
        was_created = False
    else:
        document = Document(
            id=doc_id,
            tenant_id=tenant.id,
            owner_ref=owner_ref,
            file_name=file_name,
            document_type=document_type,
            original_document_type=document_type,
            mime_type=mime_type,
            file_size=len(file_bytes),
            # Seed with a presigned URL so the column has a valid value;
            # read paths regenerate it via refresh_document_urls.
            s3_url=initial_presigned,
            s3_url_ocr_json=None,
            group_tag=group_tag,
            ocr_status=ocr_status,
            ocr_text=None,
            ocr_error=None,
            base64_status=Base64Status.PENDING,
            base64_error=None,
            metadata_=metadata,
            uploaded_by=uploaded_by,
        )
        db.add(document)
        was_created = True

    await db.commit()
    await db.refresh(document)

    # Always kick off the base64 push (the second pipeline). Independent of OCR.
    from app.tasks.document_tasks import push_document_base64

    push_document_base64.delay(str(document.id))

    if queue_ocr:
        # Avoid Celery import at module load to keep test imports cheap.
        from app.tasks.document_tasks import ocr_document

        ocr_document.delay(str(document.id))
    else:
        # Spec §7: upload with ocr=false fires notification immediately.
        refresh_document_urls(document)
        notifier.notify_doc_processed(tenant, document)

    refresh_document_urls(document)
    return document, was_created


async def get_document_for_tenant(
    db: AsyncSession, *, tenant: Tenant, doc_id: uuid.UUID
) -> Document:
    stmt = select(Document).where(Document.id == doc_id, Document.tenant_id == tenant.id)
    document = (await db.execute(stmt)).scalar_one_or_none()
    if document is None:
        # Spec §8: never 403 — don't leak existence across tenants.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


async def delete_document(db: AsyncSession, document: Document) -> None:
    s3 = S3Service()
    s3.delete_prefix(f"{document.tenant_id}/{document.owner_ref}/{document.id}/")
    await db.delete(document)
    await db.commit()


async def enqueue_ocr(
    db: AsyncSession, *, tenant: Tenant, document: Document, force: bool
) -> tuple[Document, bool]:
    """
    Returns (document, was_enqueued).

    Spec §6.6 transitions:
      not_requested / failed -> queue
      done                   -> queue only if force=True
      pending                -> no-op (force is ignored, spec §12)
    """
    if document.ocr_status == OcrStatus.PENDING:
        return document, False
    if document.ocr_status == OcrStatus.DONE and not force:
        return document, False

    document.ocr_status = OcrStatus.PENDING
    document.ocr_error = None
    await db.commit()
    await db.refresh(document)

    from app.tasks.document_tasks import ocr_document

    ocr_document.delay(str(document.id))
    return document, True
