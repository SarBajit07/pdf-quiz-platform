"""Persist PDF cleanup across document deletion and worker restarts."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_file_deletions",
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pending_file_deletions")
