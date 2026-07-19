import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    owner_ref: str
    file_name: str
    document_type: str
    original_document_type: str
    mime_type: str
    file_size: int
    s3_url: str
    s3_url_ocr_json: str | None = None
    group_tag: uuid.UUID | None = None
    ocr_status: Literal["not_requested", "pending", "done", "failed"]
    ocr_text: str | None = None
    ocr_error: str | None = None
    base64_status: Literal["pending", "sent", "failed"]
    base64_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    uploaded_by: str
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    results: list[DocumentRead]
    total: int
    next_offset: int | None = None


class DocumentPatch(BaseModel):
    document_type: str | None = None
    metadata: dict[str, Any] | None = None
    uploaded_by: str | None = None


class OcrTriggerRequest(BaseModel):
    force: bool = False


class OcrTextResponse(BaseModel):
    ocr_status: Literal["not_requested", "pending", "done", "failed"]
    ocr_text: str | None = None
    ocr_error: str | None = None
