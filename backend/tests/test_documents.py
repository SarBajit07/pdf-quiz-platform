import asyncio
import io
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pypdfium2 import PdfDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import worker
from app.core.config import settings
from app.core.security import make_token
from app.database import get_session
from app.documents import extract_pages
from app.llm import LLMClient
from app.main import app
from app.models import Document, DocumentPage, Job, Question, Quiz, User
from app.upload_limit import UploadLimitMiddleware
from app.worker import process_next_job


async def test_worker_reuses_llm_client_across_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[LLMClient] = []

    async def process_job(*, client: LLMClient) -> bool:
        if len(clients) == 2:
            raise asyncio.CancelledError
        clients.append(client)
        return True

    monkeypatch.setattr(worker, "process_next_job", process_job)
    with pytest.raises(asyncio.CancelledError):
        await worker.main()
    assert clients[0] is clients[1]


async def test_timeout_kills_child_and_allows_next_extraction(tmp_path, monkeypatch):
    spawn = asyncio.create_subprocess_exec
    children = []

    async def slow_child(*args, **kwargs):
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(process)
        return process

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_subprocess_exec", slow_child)
        patch.setattr(settings, "extraction_timeout_seconds", 0.1)
        with pytest.raises(TimeoutError):
            await worker.run_extractor("unused.pdf")
    assert children[0].returncode is not None
    pdf = PdfDocument.new()
    pdf.new_page(200, 200)
    path = tmp_path / "next.pdf"
    pdf.save(str(path))
    pdf.close()
    assert await worker.run_extractor(str(path)) == [""]


def test_extract_blank_page_and_reject_invalid_pdf() -> None:
    pdf = PdfDocument.new()
    pdf.new_page(200, 200)
    output = io.BytesIO()
    pdf.save(output)
    pdf.close()
    assert extract_pages(output.getvalue()) == [""]
    with pytest.raises(ValueError):
        extract_pages(b"%PDF-invalid")


def test_page_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_pages", 1)
    pdf = PdfDocument.new()
    pdf.new_page(200, 200)
    pdf.new_page(200, 200)
    output = io.BytesIO()
    pdf.save(output)
    pdf.close()
    with pytest.raises(ValueError, match="page count"):
        extract_pages(output.getvalue())


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
async def test_streamed_body_limit_before_multipart_parsing(monkeypatch, headers) -> None:
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    chunks = iter([
        {"type": "http.request", "body": b"x" * 40000, "more_body": True},
        {"type": "http.request", "body": b"x" * 40000, "more_body": True},
    ])
    sent = []

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    await UploadLimitMiddleware(downstream)(
        {"type": "http", "method": "POST", "path": "/api/v1/documents", "headers": headers},
        receive, send,
    )
    assert sent[0]["status"] == 413
    assert not called


@pytest.fixture
async def document_sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated PostgreSQL test database")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            yield async_sessionmaker(
                connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_upload_worker_failure_and_ownership(document_sessions, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    async def override_session():
        async with document_sessions() as session:
            yield session

    async with document_sessions.begin() as session:
        owner, other = [
            User(email=f"{uuid4()}@example.com", password_hash="test") for _ in range(2)
        ]
        session.add_all([owner, other])
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {make_token(owner.id, 'access')}"}
            pdf = PdfDocument.new()
            pdf.new_page(200, 200)
            output = io.BytesIO()
            pdf.save(output)
            pdf.close()
            response = await client.post("/api/v1/documents", headers=headers,
                                         files={"file": ("test.pdf", output.getvalue(),
                                                          "application/pdf")})
            assert response.status_code == 201
            document_id = response.json()["id"]
            assert response.json()["status"] == "queued"
            # Simulate a worker that died after claiming the durable job.
            async with document_sessions.begin() as session:
                job = await session.scalar(select(Job))
                job.status = "processing"
                job.started_at = datetime.now(UTC) - timedelta(minutes=16)
                job.attempts_count = 1
            assert await process_next_job(document_sessions)
            result = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
            assert result.json()["status"] == "failed"
            assert result.json()["page_count"] == 1
            async with document_sessions() as session:
                page = await session.scalar(select(DocumentPage))
                assert page.page_number == 1 and not page.has_text
                assert (await session.scalar(select(Job))).status == "failed"
                assert (await session.scalar(select(Job))).attempts_count == 2
            other_headers = {"Authorization": f"Bearer {make_token(other.id, 'access')}"}
            assert (await client.get("/api/v1/documents", headers=other_headers)).json() == []
            assert (await client.get(f"/api/v1/documents/{document_id}",
                                     headers=other_headers)).status_code == 404
            response = await client.post("/api/v1/documents", headers=headers,
                                         files={"file": ("bad.pdf", b"%PDF-invalid",
                                                          "application/pdf")})
            assert response.status_code == 201
            assert await process_next_job(document_sessions)
            result = await client.get(f"/api/v1/documents/{response.json()['id']}", headers=headers)
            assert result.json()["status"] == "failed"
            assert result.json()["error_message"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("failure", [OSError, TimeoutError, ValueError])
async def test_worker_retries_with_backoff_and_stops(document_sessions, monkeypatch, failure):
    monkeypatch.setattr(settings, "extraction_max_retries", 2)
    calls = 0

    async def fail(path):
        nonlocal calls
        calls += 1
        raise failure("test failure")

    monkeypatch.setattr(worker, "run_extractor", fail)
    async with document_sessions.begin() as session:
        user = User(email=f"{uuid4()}@example.com", password_hash="test")
        session.add(user)
        await session.flush()
        document = Document(user_id=user.id, filename="test.pdf", storage_path="test.pdf",
                            size_bytes=100, page_count=0, doc_type_source="auto")
        session.add(document)
        await session.flush()
        job = Job(document_id=document.id)
        session.add(job)
        await session.flush()
        job_id, document_id = job.id, document.id

    for attempt in range(1, 4):
        assert await process_next_job(document_sessions)
        async with document_sessions() as session:
            job = await session.get(Job, job_id)
            document = await session.get(Document, document_id)
            if failure is ValueError or attempt == 3:
                assert job.status == document.status == "failed"
                assert document.error_message
                assert calls == attempt
                break
            assert job.status == document.status == "queued"
            assert (job.next_attempt_at - job.finished_at).total_seconds() == (
                settings.extraction_retry_seconds * 2 ** (attempt - 1)
            )
        assert not await process_next_job(document_sessions)
        async with document_sessions.begin() as session:
            job = await session.get(Job, job_id)
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not await process_next_job(document_sessions)


async def test_worker_stores_text_with_nul_characters(document_sessions, monkeypatch):
    async def extracted_pages(path):
        return ["\u0000", "a\u0000b", "\u0000 \n\t", ""]

    monkeypatch.setattr(worker, "run_extractor", extracted_pages)
    async with document_sessions.begin() as session:
        user = User(email=f"{uuid4()}@example.com", password_hash="test")
        session.add(user)
        await session.flush()
        document = Document(user_id=user.id, filename="nul.pdf", storage_path="nul.pdf",
                            size_bytes=100, page_count=0, doc_type_source="auto")
        session.add(document)
        await session.flush()
        job = Job(document_id=document.id)
        session.add(job)
        await session.flush()
        document_id, job_id = document.id, job.id

    assert await process_next_job(document_sessions)
    async with document_sessions() as session:
        document = await session.get(Document, document_id)
        job = await session.get(Job, job_id)
        assert document.status == job.status == "failed"
        assert document.page_count == 4
        assert "extractable text" in document.error_message
        assert job.attempts_count == 1
        pages = list(await session.scalars(
            select(DocumentPage).where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number)
        ))
        assert [page.page_number for page in pages] == [1, 2, 3, 4]
        assert [page.text for page in pages] == ["", "ab", " \n\t", ""]
        assert [page.has_text for page in pages] == [False, True, False, False]


async def test_worker_saves_quiz_before_ready(document_sessions, monkeypatch):
    from app.generation import GeneratedQuiz

    async def extracted_pages(path):
        return ["Photosynthesis converts sunlight into chemical energy in plant leaves. "
                "Chlorophyll absorbs sunlight and the process releases oxygen."]

    async def generated(pages, count, difficulty, client):
        assert count == 5 and difficulty == "easy"
        return GeneratedQuiz([{
            "text": "What does chlorophyll absorb in plant leaves?",
            "options": ["Sunlight", "Oxygen", "Nitrogen", "Glucose"],
            "correct_index": 0, "explanation": "The notes say it absorbs sunlight.",
            "source_page": 1, "question_type": "mcq", "origin": "generated",
            "answer_source": "generated", "model_answer": None, "flagged": False,
            "question_hash": "fixture-hash",
        }], "fixture-model")

    monkeypatch.setattr(worker, "run_extractor", extracted_pages)
    monkeypatch.setattr(worker, "generate_notes", generated)
    async with document_sessions.begin() as session:
        user = User(email=f"{uuid4()}@example.com", password_hash="test")
        session.add(user)
        await session.flush()
        document = Document(user_id=user.id, filename="notes.pdf", storage_path="notes.pdf",
                            size_bytes=100, page_count=0, doc_type="notes", doc_type_source="user",
                            num_questions=5, difficulty="easy")
        session.add(document)
        await session.flush()
        session.add(Job(document_id=document.id))
        document_id = document.id

    assert await process_next_job(document_sessions)
    async with document_sessions() as session:
        document = await session.get(Document, document_id)
        quiz = await session.scalar(select(Quiz).where(Quiz.document_id == document_id))
        question = await session.scalar(select(Question).where(Question.quiz_id == quiz.id))
        assert document.status == "ready"
        assert quiz.num_questions == 1 and quiz.model == "fixture-model"
        assert question.source_page == 1 and question.correct_index == 0

    async def override_session():
        async with document_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {make_token(user.id, 'access')}"}
            detail = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
            listing = await client.get("/api/v1/documents", headers=headers)
            assert detail.status_code == listing.status_code == 200
            assert detail.json() == listing.json()[0]
            assert detail.json()["num_questions"] == 5
            assert detail.json()["generated_question_count"] == 1
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_quiz_write_failure_records_job_failure_and_keeps_existing_quiz(
    document_sessions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.generation import GeneratedQuiz

    question = {
        "text": "What does chlorophyll absorb in plant leaves?",
        "options": ["Sunlight", "Oxygen", "Nitrogen", "Glucose"],
        "correct_index": 0, "explanation": "The notes say it absorbs sunlight.",
        "source_page": 1, "question_type": "mcq", "origin": "generated",
        "answer_source": "generated", "model_answer": None, "flagged": False,
        "question_hash": "stored-question",
    }

    async def generated(pages, count, difficulty, client) -> GeneratedQuiz:
        return GeneratedQuiz([{**question, "correct_index": 4}], "fixture-model")

    monkeypatch.setattr(worker, "generate_notes", generated)
    async with document_sessions.begin() as session:
        user = User(email=f"{uuid4()}@example.com", password_hash="test")
        session.add(user)
        await session.flush()
        document = Document(user_id=user.id, filename="notes.pdf", storage_path="notes.pdf",
                            size_bytes=100, page_count=1, doc_type="notes", doc_type_source="user")
        session.add(document)
        await session.flush()
        session.add_all([
            Job(document_id=document.id),
            DocumentPage(document_id=document.id, page_number=1, has_text=True,
                         text="Chlorophyll absorbs sunlight and photosynthesis releases oxygen."),
        ])
        original = Quiz(document_id=document.id, user_id=user.id, title="notes",
                        num_questions=1, model="original-model")
        session.add(original)
        await session.flush()
        session.add(Question(quiz_id=original.id, position=1, **question))
        document_id, quiz_id = document.id, original.id

    assert await process_next_job(document_sessions)
    async with document_sessions() as session:
        document = await session.get(Document, document_id)
        job = await session.scalar(select(Job).where(Job.document_id == document_id))
        quizzes = list(await session.scalars(select(Quiz).where(Quiz.document_id == document_id)))
        stored = await session.scalar(select(Question).where(Question.quiz_id == quiz_id))
        assert job.status == document.status == "failed"
        assert job.finished_at is not None and job.next_attempt_at is None
        assert job.error == document.error_message == "Unable to save processing results"
        assert [quiz.id for quiz in quizzes] == [quiz_id]
        assert stored.correct_index == 0


async def test_low_confidence_requires_owner_confirmation(document_sessions, monkeypatch):
    from app.generation import GeneratedQuiz

    async def extracted_pages(path):
        return ["This document discusses photosynthesis and chlorophyll in plant leaves. "
                "Sunlight is converted into chemical energy and oxygen is released."]

    async def classify(pages, client):
        return "notes", 0.55

    async def generated(pages, count, difficulty, client):
        return GeneratedQuiz([{
            "text": "What does chlorophyll absorb in plant leaves?",
            "options": ["Sunlight", "Oxygen", "Nitrogen", "Glucose"],
            "correct_index": 0, "explanation": "The notes say it absorbs sunlight.",
            "source_page": 1, "question_type": "mcq", "origin": "generated",
            "answer_source": "generated", "model_answer": None, "flagged": False,
            "question_hash": "confirmed-hash",
        }], "fixture-model")

    monkeypatch.setattr(worker, "run_extractor", extracted_pages)
    monkeypatch.setattr(worker, "classify_document", classify)
    monkeypatch.setattr(worker, "generate_notes", generated)
    async with document_sessions.begin() as session:
        owner = User(email=f"{uuid4()}@example.com", password_hash="test")
        session.add(owner)
        await session.flush()
        document = Document(user_id=owner.id, filename="auto.pdf", storage_path="auto.pdf",
                            size_bytes=100, page_count=0, doc_type_source="auto")
        session.add(document)
        await session.flush()
        session.add(Job(document_id=document.id))
        document_id = document.id

    assert await process_next_job(document_sessions)
    async with document_sessions() as session:
        document = await session.get(Document, document_id)
        assert document.status == "needs_confirmation"
        assert document.doc_type_confidence == 0.55
        assert await session.scalar(select(Quiz).where(Quiz.document_id == document_id)) is None

    async def override_session():
        async with document_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"/api/v1/documents/{document_id}/type",
                headers={"Authorization": f"Bearer {make_token(owner.id, 'access')}"},
                json={"doc_type": "notes"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "queued"
            assert response.json()["doc_type_source"] == "user"
    finally:
        app.dependency_overrides.clear()
    assert await process_next_job(document_sessions)
    async with document_sessions() as session:
        assert (await session.get(Document, document_id)).status == "ready"
