"""
Collection-portal JSON API (Phase 2).

`POST /v1/document-requests` mints one upload link per doc type for a client
reference and returns them. Authenticated with the tenant API key, same as
the rest of the v1 surface. The staff web page (app/web) is a UI over this
same service layer.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant, get_db
from app.core import doc_types
from app.models.tenant import Tenant
from app.schemas.document_request import (
    BatchOut,
    DocumentRequestCreate,
    LinkOut,
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
