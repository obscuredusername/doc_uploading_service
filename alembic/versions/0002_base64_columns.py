"""add base64_status + base64_error columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document",
        sa.Column(
            "base64_status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
    )
    op.add_column(
        "document",
        sa.Column("base64_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document", "base64_error")
    op.drop_column("document", "base64_status")
