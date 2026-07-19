import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OcrStatus(str):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class Base64Status(str):
    """Lifecycle of the parallel base64-push pipeline.

    Every upload defaults to PENDING — a Celery task encodes the file and
    POSTs it to the external base64-storage service. SENT on success, FAILED
    after retries are exhausted.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_ref",
            "file_name",
            "document_type",
            name="document_dedup_key",
        ),
        Index("document_owner_idx", "tenant_id", "owner_ref"),
        Index(
            "document_group_idx",
            "group_tag",
            postgresql_where=text("group_tag IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_ref: Mapped[str] = mapped_column(String, nullable=False)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    document_type: Mapped[str] = mapped_column(String, nullable=False)
    original_document_type: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    s3_url: Mapped[str] = mapped_column(String, nullable=False)
    s3_url_ocr_json: Mapped[str | None] = mapped_column(String, nullable=True)
    group_tag: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ocr_status: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'not_requested'")
    )
    ocr_text: Mapped[str | None] = mapped_column(String, nullable=True)
    ocr_error: Mapped[str | None] = mapped_column(String, nullable=True)
    base64_status: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'pending'")
    )
    base64_error: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    uploaded_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
