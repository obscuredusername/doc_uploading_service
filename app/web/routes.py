"""
Collection-portal web UI (Phase 3) — served by the same FastAPI app / port.

Pages:
  GET  /staff                         staff search form
  POST /staff/generate                mint links, render them as upload tabs
  GET  /u/{ref}/{doc_type}/{token}    standalone upload page (the client link)
  POST /u/{ref}/{doc_type}/{token}    handle an upload (JSON; used by the JS)
  GET  /files/{path}                  serve a locally-stored file

The upload form is a single partial reused both as a tab panel and on the
standalone page — no duplicated frontend.
"""
import logging
import mimetypes

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.core import doc_types
from app.models.document import Document
from app.models.tenant import Tenant
from app.services import request_service
from app.services.storage import get_storage_backend
from app.web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portal-web"])


def _link_view(req) -> dict:
    return {
        "doc_type": req.doc_type,
        "label": doc_types.label_for(req.doc_type),
        "url": request_service.build_link_url(req.reference, req.doc_type, req.token),
        "reference": req.reference,
        "token": req.token,
        "status": req.status,
        "is_live": req.is_live,
    }


@router.get("/staff")
async def staff_home(request: Request):
    return templates.TemplateResponse("search.html", {"request": request})


@router.post("/staff/generate")
async def staff_generate(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    name = (form.get("name") or "").strip()
    reference = (form.get("reference") or "").strip()

    if not name or not reference:
        return templates.TemplateResponse(
            "search.html",
            {"request": request, "error": "Both name and reference are required."},
        )

    tenant = await request_service.get_portal_tenant(db)
    _batch_id, requests = await request_service.create_request_batch(
        db, tenant=tenant, name=name, reference=reference
    )
    return templates.TemplateResponse(
        "tabs.html",
        {
            "request": request,
            "name": name,
            "reference": reference,
            "links": [_link_view(r) for r in requests],
        },
    )


@router.get("/u/{reference}/{doc_type}/{token}")
async def upload_page(
    request: Request,
    reference: str,
    doc_type: str,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    req = await request_service.get_by_token(
        db, reference=reference, doc_type=doc_type, token=token
    )
    if req is None:
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "state": "invalid", "label": doc_types.label_for(doc_type)},
            status_code=404,
        )
    state = "live" if req.is_live else req.status  # "submitted" | "expired" | "pending(expired)"
    if state == "pending":
        state = "expired"
    return templates.TemplateResponse(
        "upload.html",
        {"request": request, "state": state, "link": _link_view(req)},
    )


@router.post("/u/{reference}/{doc_type}/{token}")
async def upload_submit(
    reference: str,
    doc_type: str,
    token: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    req = await request_service.get_by_token(
        db, reference=reference, doc_type=doc_type, token=token
    )
    if req is None:
        return JSONResponse({"ok": False, "message": "Link not found."}, status_code=404)
    if not req.is_live:
        reason = "already submitted" if req.status == "submitted" else "expired"
        return JSONResponse(
            {"ok": False, "message": f"This link is {reason}."}, status_code=409
        )

    file_bytes = await file.read()
    try:
        doc = await request_service.submit_upload(
            db,
            req=req,
            file_bytes=file_bytes,
            file_name=file.filename or "unnamed",
            mime_type=file.content_type or "application/octet-stream",
        )
    except Exception as exc:  # surfaces validation (size/mime) as a message
        detail = getattr(exc, "detail", None) or str(exc)
        return JSONResponse({"ok": False, "message": str(detail)}, status_code=400)

    return JSONResponse(
        {"ok": True, "message": f"Uploaded {doc.file_name}.", "doc_id": str(doc.id)}
    )


@router.get("/files/{key:path}")
async def serve_file(key: str, db: AsyncSession = Depends(get_db)):
    """Public file serving for locally-stored uploads (spec decision #5).

    Serves with the file's real content type and an *inline* disposition so
    browsers preview PDFs/images in the tab instead of downloading them.
    """
    if not settings.public_files:
        return Response(status_code=404)

    backend = get_storage_backend("local")
    try:
        stream = backend.open_stream(key)
    except (FileNotFoundError, ValueError):
        return Response(status_code=404)

    # Prefer the exact mime type recorded on the Document row; fall back to
    # guessing from the filename extension.
    public_url = backend.public_url(key)
    doc = (
        await db.execute(select(Document).where(Document.s3_url == public_url))
    ).scalar_one_or_none()
    if doc is not None:
        media_type = doc.mime_type
        filename = doc.file_name
    else:
        media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        filename = key.rsplit("/", 1)[-1].split("original__")[-1]

    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
