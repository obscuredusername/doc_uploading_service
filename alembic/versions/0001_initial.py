"""initial schema: tenant + document

Revision ID: 0001
Revises:
Create Date: 2026-05-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False, unique=True),
        sa.Column("broker_url", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "document",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_ref", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("original_document_type", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("s3_url", sa.Text(), nullable=False),
        sa.Column("s3_url_ocr_json", sa.Text(), nullable=True),
        sa.Column("group_tag", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "ocr_status",
            sa.Text(),
            server_default=sa.text("'not_requested'"),
            nullable=False,
        ),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("ocr_error", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_ref",
            "file_name",
            "document_type",
            name="document_dedup_key",
        ),
    )

    op.create_index("document_owner_idx", "document", ["tenant_id", "owner_ref"])
    op.create_index(
        "document_group_idx",
        "document",
        ["group_tag"],
        postgresql_where=sa.text("group_tag IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("document_group_idx", table_name="document")
    op.drop_index("document_owner_idx", table_name="document")
    op.drop_table("document")
    op.drop_table("tenant")
