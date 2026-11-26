"""Store generation choices and classification confirmation state."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column(
        "num_questions", sa.Integer(), server_default="10", nullable=False,
    ))
    op.add_column("documents", sa.Column(
        "difficulty", sa.String(), server_default="medium", nullable=False,
    ))
    op.create_check_constraint(
        "ck_document_num_questions", "documents", "num_questions BETWEEN 5 AND 30",
    )
    op.create_check_constraint(
        "ck_document_difficulty", "documents", "difficulty IN ('easy','medium','hard')",
    )
    op.drop_constraint("ck_document_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_document_status", "documents",
        "status IN ('queued','processing','needs_confirmation','ready','failed')",
    )
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_job_status", "jobs",
        "status IN ('queued','processing','needs_confirmation','ready','failed')",
    )
    # Before this revision, ready meant that the document had been extracted.
    # Keep the stored pages and give the new generation job a full retry budget.
    op.execute("""
        UPDATE jobs
        SET status = 'queued', attempts_count = 0, started_at = NULL,
            finished_at = NULL, next_attempt_at = NULL, error = NULL
        WHERE id IN (
            SELECT DISTINCT ON (job.document_id) job.id
            FROM jobs AS job JOIN documents ON documents.id = job.document_id
            WHERE documents.status = 'ready'
              AND NOT EXISTS (SELECT 1 FROM quizzes WHERE document_id = documents.id)
            ORDER BY job.document_id, job.created_at DESC, job.id DESC
        )
    """)
    op.execute("""
        INSERT INTO jobs (document_id)
        SELECT id FROM documents
        WHERE status = 'ready'
          AND NOT EXISTS (SELECT 1 FROM quizzes WHERE document_id = documents.id)
          AND NOT EXISTS (SELECT 1 FROM jobs WHERE document_id = documents.id)
    """)
    op.execute("""
        UPDATE documents
        SET status = 'queued', error_message = NULL
        WHERE status = 'ready'
          AND NOT EXISTS (SELECT 1 FROM quizzes WHERE document_id = documents.id)
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE jobs
        SET status = 'queued', attempts_count = 0, started_at = NULL,
            finished_at = NULL, next_attempt_at = NULL, error = NULL
        WHERE status = 'needs_confirmation'
    """)
    op.execute("""
        UPDATE documents
        SET status = 'queued', doc_type = NULL, doc_type_confidence = NULL, error_message = NULL
        WHERE status = 'needs_confirmation'
    """)
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_job_status", "jobs", "status IN ('queued','processing','ready','failed')",
    )
    op.drop_constraint("ck_document_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_document_status", "documents", "status IN ('queued','processing','ready','failed')",
    )
    op.drop_constraint("ck_document_difficulty", "documents", type_="check")
    op.drop_constraint("ck_document_num_questions", "documents", type_="check")
    op.drop_column("documents", "difficulty")
    op.drop_column("documents", "num_questions")
