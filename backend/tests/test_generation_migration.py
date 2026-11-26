import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine


async def test_generation_migration_requeues_legacy_jobs_and_pending_confirmations():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")

    def round_trip(connection: Connection) -> None:
        schema = f"test_generation_migration_{uuid4().hex}"
        connection.execute(sa.schema.CreateSchema(schema))
        connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
        revisions = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations"))
        with Operations.context(MigrationContext.configure(connection)):
            for revision in ("0001", "0002", "0003"):
                revisions.get_revision(revision).module.upgrade()
            documents = dict(connection.exec_driver_sql("""
                WITH owner AS (
                    INSERT INTO users (email, password_hash)
                    VALUES ('migration@example.test', 'test') RETURNING id
                )
                INSERT INTO documents
                    (user_id, filename, storage_path, size_bytes, page_count,
                     doc_type_source, status)
                SELECT id, filename, filename, 100, 1, 'auto', 'ready'
                FROM owner CROSS JOIN
                    (VALUES ('legacy.pdf'), ('with-quiz.pdf'), ('orphan.pdf')) AS f(filename)
                RETURNING filename, id
            """).tuples().all())
            legacy_id = documents["legacy.pdf"]
            with_quiz_id = documents["with-quiz.pdf"]
            connection.exec_driver_sql("""
                INSERT INTO jobs
                    (document_id, status, attempts_count, started_at, finished_at, next_attempt_at)
                SELECT id, 'ready', 4, now(), now(), now()
                FROM documents WHERE filename <> 'orphan.pdf'
            """)
            connection.execute(sa.text("""
                INSERT INTO quizzes (document_id, user_id, title, num_questions, model)
                SELECT id, user_id, filename, 5, 'fixture' FROM documents WHERE id = :id
            """), {"id": with_quiz_id})
            connection.execute(sa.text("""
                INSERT INTO document_pages (document_id, page_number, text, has_text)
                VALUES (:id, 1, 'Stored notes', true)
            """), {"id": legacy_id})

            generation = revisions.get_revision("0004").module
            generation.upgrade()

            def statuses(document_id: UUID) -> tuple:
                row = connection.execute(sa.text("""
                    SELECT documents.status, jobs.status, attempts_count,
                           started_at, finished_at, next_attempt_at
                    FROM documents JOIN jobs ON jobs.document_id = documents.id
                    WHERE documents.id = :id
                """), {"id": document_id}).one()
                return tuple(row)

            assert statuses(legacy_id) == ("queued", "queued", 0, None, None, None)
            assert statuses(documents["orphan.pdf"]) == ("queued", "queued", 0, None, None, None)
            assert statuses(with_quiz_id)[:3] == ("ready", "ready", 4)
            assert connection.exec_driver_sql("SELECT text FROM document_pages").scalar_one() == (
                "Stored notes"
            )

            connection.execute(sa.text("""
                UPDATE documents SET status = 'needs_confirmation', doc_type = 'notes',
                    doc_type_confidence = 0.5 WHERE id = :id
            """), {"id": legacy_id})
            connection.execute(sa.text("""
                UPDATE jobs SET status = 'needs_confirmation', attempts_count = 1,
                    started_at = now(), finished_at = now() WHERE document_id = :id
            """), {"id": legacy_id})
            generation.downgrade()
            assert statuses(legacy_id) == ("queued", "queued", 0, None, None, None)
            assert connection.execute(sa.text("""
                SELECT doc_type, doc_type_confidence FROM documents WHERE id = :id
            """), {"id": legacy_id}).one() == (None, None)

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(round_trip)
            await connection.rollback()
    finally:
        await engine.dispose()
