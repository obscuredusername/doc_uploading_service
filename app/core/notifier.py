"""
Per-tenant Celery notification publisher (spec §7).

The microservice publishes `microservice.doc_processed` to the tenant's
broker URL. Each tenant has its own Celery client, cached by broker URL.
"""
import logging
from typing import Any

from celery import Celery

from app.models.document import Document
from app.models.tenant import Tenant
from app.services.s3_service import refresh_document_urls

logger = logging.getLogger(__name__)

NOTIFICATION_TASK_NAME = "microservice.doc_processed"
SCHEMA_VERSION = 1

_clients: dict[str, Celery] = {}


def _client_for(broker_url: str) -> Celery:
    client = _clients.get(broker_url)
    if client is None:
        client = Celery(broker=broker_url)
        _clients[broker_url] = client
    return client


def build_payload(document: Document) -> dict[str, Any]:
    # Make sure s3_url / s3_url_ocr_json are fresh presigned URLs at publish time.
    refresh_document_urls(document)
    return {
        "schema_version": SCHEMA_VERSION,
        "event": "doc_processed",
        "doc_id": str(document.id),
        "tenant_id": str(document.tenant_id),
        "owner_ref": document.owner_ref,
        "file_name": document.file_name,
        "document_type": document.document_type,
        "original_document_type": document.original_document_type,
        "s3_url": document.s3_url,
        "s3_url_ocr_json": document.s3_url_ocr_json,
        "ocr_status": document.ocr_status,
        "base64_status": document.base64_status,
        "group_tag": str(document.group_tag) if document.group_tag else None,
        "uploaded_by": document.uploaded_by,
        "metadata": document.metadata_ or {},
    }


def notify_doc_processed(tenant: Tenant, document: Document) -> None:
    """
    Fire `microservice.doc_processed` to the tenant's broker.

    Failures are logged and swallowed — the document is already persisted;
    we don't want a broker outage to fail the user's request. Reliability
    improvements (outbox table, retry queue) belong to v2.
    """
    payload = build_payload(document)
    try:
        _client_for(tenant.broker_url).send_task(NOTIFICATION_TASK_NAME, args=[payload])
    except Exception:
        logger.exception(
            "notify_doc_processed failed: tenant=%s doc=%s broker=%s",
            tenant.id,
            document.id,
            tenant.broker_url,
        )
