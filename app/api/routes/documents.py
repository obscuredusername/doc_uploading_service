import json
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant, get_db
from app.config import settings
from app.core.rate_limit import UPLOAD_LIMIT, limiter
from app.models.document import Document, OcrStatus
from app.models.tenant import Tenant
from app.schemas.document import (
    DocumentList,
    DocumentPatch,
    DocumentRead,
    OcrTextResponse,
    OcrTriggerRequest,
)
from app.services import document_service, request_service
from app.services.s3_service import (
    S3Service,
    build_ocr_json_key,
    build_original_key,
    refresh_document_urls,
)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

PATCHABLE_FIELDS = {"document_type", "metadata", "uploaded_by"}


def _to_read(document) -> DocumentRead:
    """Serialize a Document, refreshing S3 URLs when applicable.

    For S3-backed docs this swaps in a fresh presigned URL; for local-storage
    docs the stored /files/... URL is kept (see refresh_document_urls).
    """
    refresh_document_urls(document)
    return DocumentRead.model_validate(document)


def _parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"metadata is not valid JSON: {e}",
        )
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="metadata must be a JSON object",
        )
    return value


def _parse_uuid_or_400(value: str | None, field: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} is not a valid UUID",
        )


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(UPLOAD_LIMIT)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    owner_ref: Annotated[str, Form(...)],
    document_type: Annotated[str, Form(...)],
    uploaded_by: Annotated[str, Form(...)],
    ocr: Annotated[bool, Form()] = False,
    client_name: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form()] = None,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document for a case.

    Stored on the local filesystem under <owner_ref>__<name>/<document_type>/.
    When ``ocr=true`` the file is OCR'd synchronously and a JSON sidecar is
    written next to it (and exposed via ``s3_url_ocr_json``).
    """
    file_bytes = await file.read()
    # Folder name for the case: explicit client_name > name from generated
    # links for this reference > uploaded_by.
    name = (
        client_name
        or await request_service.client_name_for_reference(
            db, tenant=tenant, reference=owner_ref
        )
        or uploaded_by
    )
    document = await request_service.store_local_document(
        db,
        tenant_id=tenant.id,
        reference=owner_ref,
        client_name=name,
        doc_type=document_type,
        uploaded_by=uploaded_by,
        file_bytes=file_bytes,
        file_name=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        run_ocr=ocr,
        metadata=_parse_metadata(metadata),
    )
    await db.commit()
    await db.refresh(document)
    return _to_read(document)


@router.get("", response_model=DocumentList)
async def list_documents(
    owner_ref: str | None = Query(default=None),
    group_tag: str | None = Query(default=None),
    document_type: str | None = Query(default=None),
    ocr_status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    # Spec §6.3: at least one filter required.
    if not any([owner_ref, group_tag, document_type, ocr_status]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of owner_ref, group_tag, document_type, ocr_status is required",
        )

    filters = [Document.tenant_id == tenant.id]
    if owner_ref:
        filters.append(Document.owner_ref == owner_ref)
    if group_tag:
        filters.append(Document.group_tag == _parse_uuid_or_400(group_tag, "group_tag"))
    if document_type:
        filters.append(Document.document_type == document_type)
    if ocr_status:
        filters.append(Document.ocr_status == ocr_status)

    total = (await db.execute(select(func.count()).select_from(Document).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(Document).where(*filters).order_by(Document.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    next_offset = offset + limit if (offset + limit) < total else None
    return DocumentList(
        results=[_to_read(r) for r in rows],
        total=total,
        next_offset=next_offset,
    )


@router.get("/{doc_id}", response_model=DocumentRead)
async def get_document(
    doc_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)
    return _to_read(document)


@router.patch("/{doc_id}", response_model=DocumentRead)
async def patch_document(
    doc_id: uuid.UUID,
    body: DocumentPatch,
    metadata_mode: str = Query(default="merge", pattern="^(merge|replace)$"),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)

    payload = body.model_dump(exclude_unset=True)
    illegal = set(payload) - PATCHABLE_FIELDS
    if illegal:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fields are immutable: {sorted(illegal)}",
        )

    if "document_type" in payload:
        document.document_type = payload["document_type"]
    if "uploaded_by" in payload:
        document.uploaded_by = payload["uploaded_by"]
    if "metadata" in payload:
        new_meta = payload["metadata"] or {}
        if metadata_mode == "replace":
            document.metadata_ = new_meta
        else:
            document.metadata_ = {**(document.metadata_ or {}), **new_meta}

    await db.commit()
    await db.refresh(document)
    return _to_read(document)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)
    await document_service.delete_document(db, document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{doc_id}/ocr", response_model=DocumentRead)
async def trigger_ocr(
    doc_id: uuid.UUID,
    body: OcrTriggerRequest = OcrTriggerRequest(),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)
    document, was_enqueued = await document_service.enqueue_ocr(
        db, tenant=tenant, document=document, force=body.force
    )
    # Spec §6.6: 202 when queued, 200 when no-op.
    return Response(
        content=DocumentRead.model_validate(document).model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED if was_enqueued else status.HTTP_200_OK,
    )


@router.get("/{doc_id}/text", response_model=OcrTextResponse)
async def get_document_text(
    doc_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)

    if document.ocr_status == OcrStatus.DONE:
        return OcrTextResponse(ocr_status="done", ocr_text=document.ocr_text)
    if document.ocr_status == OcrStatus.PENDING:
        return Response(
            content=OcrTextResponse(ocr_status="pending").model_dump_json(),
            media_type="application/json",
            status_code=status.HTTP_202_ACCEPTED,
        )
    if document.ocr_status == OcrStatus.FAILED:
        return Response(
            content=OcrTextResponse(
                ocr_status="failed", ocr_error=document.ocr_error
            ).model_dump_json(),
            media_type="application/json",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    # not_requested — same shape as "done" but with null text.
    return OcrTextResponse(ocr_status="not_requested")


@router.get("/{doc_id}/file")
async def stream_document_file(
    doc_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Auth-proxy file stream. Works for both S3 and local-storage documents."""
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)

    if not settings.aws_s3_bucket_name or "/files/" in (document.s3_url or ""):
        # Local storage: derive the on-disk key from the stored /files/ URL.
        from app.services.storage import get_storage_backend

        local_key = (
            document.s3_url.split("/files/", 1)[-1]
            if document.s3_url and "/files/" in document.s3_url
            else build_original_key(
                document.tenant_id, document.owner_ref, document.id, document.file_name
            )
        )
        body_iter = get_storage_backend("local").open_stream(local_key)
    else:
        key = build_original_key(
            document.tenant_id, document.owner_ref, document.id, document.file_name
        )
        body_iter = S3Service().stream_object(key)["Body"].iter_chunks()

    return StreamingResponse(
        body_iter,
        media_type=document.mime_type,
        headers={"Content-Disposition": f'inline; filename="{document.file_name}"'},
    )


@router.get("/{doc_id}/ocr-json")
async def stream_ocr_json(
    doc_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    document = await document_service.get_document_for_tenant(db, tenant=tenant, doc_id=doc_id)
    if not document.s3_url_ocr_json:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OCR JSON not available")
    key = build_ocr_json_key(document.tenant_id, document.owner_ref, document.id)
    obj = S3Service().stream_object(key)
    return StreamingResponse(obj["Body"].iter_chunks(), media_type="application/json")
