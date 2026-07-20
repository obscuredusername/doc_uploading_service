"""
Collection-portal JSON API (Phase 2).

`POST /v1/document-requests` mints one upload link per doc type for a client
reference and returns them. Authenticated with the tenant API key, same as
the rest of the v1 surface. The staff web page (app/web) is a UI over this
same service layer.
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant, get_db
from app.core import doc_types
from app.models.tenant import Tenant
from app.schemas.document_request import (
    BatchOut,
    DocumentRequestCreate,
    LinkOut,
    RequestItemOut,
    RequestListOut,
    UploadedDocOut,
)
from app.services import request_service

router = APIRouter(prefix="/v1/document-requests", tags=["document-requests"])


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
async def create_document_requests(
    body: DocumentRequestCreate,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    batch_id, requests = await request_service.create_request_batch(
        db, tenant=tenant, name=body.name, reference=body.reference
    )
    links = [
        LinkOut(
            doc_type=req.doc_type,
            label=doc_types.label_for(req.doc_type),
            url=request_service.build_link_url(req.reference, req.doc_type, req.token),
            token=req.token,
            status=req.status,
            expires_at=req.expires_at,
        )
        for req in requests
    ]
    return BatchOut(
        batch_id=batch_id,
        reference=body.reference,
        client_name=body.name,
        links=links,
    )


@router.get("", response_model=RequestListOut)
async def list_document_requests(
    reference: str = Query(..., description="Case reference to list links for"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by link status: pending | submitted | expired",
    ),
    uploaded_only: bool = Query(
        default=False, description="Only return links that have an uploaded document"
    ),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    reqs, docs = await request_service.list_requests_for_reference(
        db, tenant=tenant, reference=reference
    )

    results: list[RequestItemOut] = []
    for req in reqs:
        eff = request_service.effective_status(req)
        if status_filter and eff != status_filter:
            continue
        doc = docs.get(req.document_id) if req.document_id else None
        if uploaded_only and doc is None:
            continue
        results.append(
            RequestItemOut(
                doc_type=req.doc_type,
                label=doc_types.label_for(req.doc_type),
                upload_url=request_service.build_link_url(
                    req.reference, req.doc_type, req.token
                ),
                token=req.token,
                status=eff,
                expires_at=req.expires_at,
                document=(
                    UploadedDocOut(
                        doc_id=doc.id,
                        file_name=doc.file_name,
                        mime_type=doc.mime_type,
                        file_url=doc.s3_url,
                        uploaded_at=doc.updated_at,
                    )
                    if doc is not None
                    else None
                ),
            )
        )

    return RequestListOut(reference=reference, total=len(results), results=results)
