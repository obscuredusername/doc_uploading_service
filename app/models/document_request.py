import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentRequestStatus(str):
    """Lifecycle of a single upload link.

    PENDING   -> link is live, waiting for the client to upload.
    SUBMITTED -> client uploaded; link is spent (one-time use, spec decision #4).
    EXPIRED   -> 7-day TTL lapsed without a submission.

    Note: a PENDING row whose ``expires_at`` is in the past is treated as
    expired at read time even before a sweep flips the stored status — see
    ``is_live``.
    """

    PENDING = "pending"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class DocumentRequest(Base):
    """One on-demand upload link: (client reference x doc type), token-guarded.

    One API call mints ~1 row per doc type, all sharing a ``batch_id``. The
    ``token`` makes the public URL unguessable (spec decision #1). When the
    client submits, ``document_id`` points at the resulting Document row.
    """

    __tablename__ = "document_request"
    __table_args__ = (
        Index("document_request_ref_idx", "tenant_id", "reference"),
        Index("document_request_batch_idx", "batch_id"),
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
    # Groups every link minted in a single API call.
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Client identity as supplied to the API.
    reference: Mapped[str] = mapped_column(String, nullable=False)
    client_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # Doc-type slug (see app/core/doc_types.py) — the URL segment.
    doc_type: Mapped[str] = mapped_column(String, nullable=False)

    # Unguessable secret embedded in the public URL.
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    status: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'pending'")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set once the client uploads. SET NULL if the Document is later deleted.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def is_live(self) -> bool:
        """True only if the link can still accept an upload right now."""
        if self.status != DocumentRequestStatus.PENDING:
            return False
        return self.expires_at > datetime.now(timezone.utc)
