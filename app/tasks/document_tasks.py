"""
Celery workers for the two background pipelines.

  - `ocr_document`: extracts text via OCR.space, writes ocr.json to S3,
    updates the document row, fires `microservice.doc_processed`.

  - `push_document_base64`: encodes the original file to base64 and POSTs
    it to the external base64-storage service. Independent of OCR; runs
    on every upload.
"""
import logging
import uuid

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import select

from app.config import settings
from app.core import notifier
from app.core.celery_app import celery_app
from app.db.session import SyncSessionLocal
from app.models.document import Base64Status, Document, OcrStatus
from app.models.tenant import Tenant
from app.services import base64_service
from app.services.base64_service import Base64PushError
from app.services.ocr import OCRError, extract_flat_text, perform_ocr
from app.services.s3_service import (
    S3Service,
    build_ocr_json_key,
    build_original_key,
    refresh_document_urls,
)

logger = logging.getLogger(__name__)


# ============================================================================
# OCR pipeline (spec §10)
# ============================================================================

@celery_app.task(
    name="documents.ocr",
    bind=True,
    autoretry_for=(),  # we control retries manually based on OCRError.transient
    max_retries=settings.ocr_max_retries,
    default_retry_delay=10,
)
def ocr_document(self, doc_id: str) -> None:
    with SyncSessionLocal() as db:
        document = db.execute(
            select(Document).where(Document.id == uuid.UUID(doc_id))
        ).scalar_one_or_none()

        if document is None:
            logger.warning("ocr_document: document %s not found", doc_id)
            return

        if document.ocr_status != OcrStatus.PENDING:
            logger.info(
                "ocr_document: doc %s status=%s — skipping",
                doc_id,
                document.ocr_status,
            )
            return

        tenant = db.execute(
            select(Tenant).where(Tenant.id == document.tenant_id)
        ).scalar_one_or_none()
        if tenant is None:
            logger.error("ocr_document: tenant %s missing for doc %s", document.tenant_id, doc_id)
            return

        s3 = S3Service()
        original_key = build_original_key(
            document.tenant_id, document.owner_ref, document.id, document.file_name
        )

        try:
            file_bytes = s3.get_object_bytes(original_key)
            is_table = document.document_type in settings.ocr_table_mode_document_types
            payload = perform_ocr(
                file_bytes,
                content_type=document.mime_type,
                is_table=is_table,
                file_name=document.file_name,
            )
        except OCRError as exc:
            if exc.transient:
                try:
                    raise self.retry(exc=exc)
                except MaxRetriesExceededError:
                    pass
            _mark_ocr_failed(db, document, str(exc))
            refresh_document_urls(document)
            notifier.notify_doc_processed(tenant, document)
            return
        except Exception as exc:  # noqa: BLE001 — final safety net
            logger.exception("ocr_document: unexpected failure for %s", doc_id)
            _mark_ocr_failed(db, document, f"unexpected: {exc}")
            refresh_document_urls(document)
            notifier.notify_doc_processed(tenant, document)
            return

        ocr_json_key = build_ocr_json_key(document.tenant_id, document.owner_ref, document.id)
        s3.upload_json(payload, ocr_json_key)

        document.ocr_status = OcrStatus.DONE
        document.ocr_text = extract_flat_text(payload)
        document.ocr_error = None
        # Seed with a presigned URL — read paths regenerate before serializing.
        document.s3_url_ocr_json = s3.generate_presigned_get(
            ocr_json_key, settings.s3_presigned_url_expires_seconds
        )
        db.commit()
        db.refresh(document)

        refresh_document_urls(document)
        notifier.notify_doc_processed(tenant, document)


def _mark_ocr_failed(db, document: Document, error: str) -> None:
    document.ocr_status = OcrStatus.FAILED
    document.ocr_error = error
    db.commit()
    db.refresh(document)


# ============================================================================
# Base64 push pipeline (parallel to S3)
# ============================================================================

@celery_app.task(
    name="documents.push_base64",
    bind=True,
    autoretry_for=(),
    max_retries=settings.base64_max_retries,
    default_retry_delay=10,
)
def push_document_base64(self, doc_id: str) -> None:
    """Encode the file and POST it to the external base64-storage service."""
    with SyncSessionLocal() as db:
        document = db.execute(
            select(Document).where(Document.id == uuid.UUID(doc_id))
        ).scalar_one_or_none()

        if document is None:
            logger.warning("push_document_base64: document %s not found", doc_id)
            return

        if document.base64_status != Base64Status.PENDING:
            logger.info(
                "push_document_base64: doc %s status=%s — skipping",
                doc_id,
                document.base64_status,
            )
            return

        s3 = S3Service()
        original_key = build_original_key(
            document.tenant_id, document.owner_ref, document.id, document.file_name
        )

        try:
            file_bytes = s3.get_object_bytes(original_key)
            payload = {
                "doc_id": str(document.id),
                "tenant_id": str(document.tenant_id),
                "filename": document.file_name,
                "mime_type": document.mime_type,
                "content_base64": base64_service.encode(file_bytes),
            }
            base64_service.push(payload)
        except Base64PushError as exc:
            if exc.transient:
                try:
                    raise self.retry(exc=exc)
                except MaxRetriesExceededError:
                    pass
            _mark_base64_failed(db, document, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("push_document_base64: unexpected failure for %s", doc_id)
            _mark_base64_failed(db, document, f"unexpected: {exc}")
            return

        document.base64_status = Base64Status.SENT
        document.base64_error = None
        db.commit()


def _mark_base64_failed(db, document: Document, error: str) -> None:
    document.base64_status = Base64Status.FAILED
    document.base64_error = error
    db.commit()
