import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentRequestCreate(BaseModel):
    """Caller/staff input: who we're collecting documents for."""

    name: str
    reference: str


class LinkOut(BaseModel):
    doc_type: str
    label: str
    url: str
    token: str
    status: str
    expires_at: datetime


class BatchOut(BaseModel):
    batch_id: uuid.UUID
    reference: str
    client_name: str | None = None
    links: list[LinkOut]


class UploadedDocOut(BaseModel):
    doc_id: uuid.UUID
    file_name: str
    mime_type: str
    file_url: str
    uploaded_at: datetime


class RequestItemOut(BaseModel):
    doc_type: str
    label: str
    upload_url: str
    token: str
    status: str  # pending | submitted | expired
    expires_at: datetime
    document: UploadedDocOut | None = None


class RequestListOut(BaseModel):
    reference: str
    total: int
    results: list[RequestItemOut]
