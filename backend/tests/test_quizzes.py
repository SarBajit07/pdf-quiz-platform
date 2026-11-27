import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import make_token
from app.database import get_session
from app.main import app
from app.models import Document, Question, Quiz, User


@pytest.fixture
async def sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated PostgreSQL test database")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            yield async_sessionmaker(connection, expire_on_commit=False)
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_quiz_hides_answers_and_scores_for_owner(sessions):
    async with sessions.begin() as session:
        owner = User(email=f"{uuid4()}@example.test", password_hash="test")
        other = User(email=f"{uuid4()}@example.test", password_hash="test")
        session.add_all([owner, other])
        await session.flush()
        doc = Document(
            user_id=owner.id, filename="study.pdf", storage_path="study.pdf", size_bytes=100,
            page_count=1, doc_type="notes", doc_type_source="user", status="failed",
        )
        session.add(doc)
        await session.flush()
        quiz = Quiz(
            document_id=doc.id, user_id=owner.id, title="Study", num_questions=3, model="test"
        )
        session.add(quiz)
        await session.flush()
        question = Question(
            quiz_id=quiz.id, position=1, question_type="mcq", origin="generated",
            answer_source="generated", flagged=False, text="Which is correct?",
            options=["A", "B", "C", "D"], correct_index=1, explanation="B is in the PDF.",
            source_page=1, question_hash="question-one",
        )
        session.add(question)
        for position, model_answer in enumerate(("Sample model answer", None), start=2):
            session.add(Question(
                quiz_id=quiz.id, position=position, question_type="short", origin="extracted",
                answer_source="none", text=f"Explain point {position}.", source_page=1,
                model_answer=model_answer, question_hash=f"question-{position}",
            ))
        await session.flush()

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner_headers = {"Authorization": f"Bearer {make_token(owner.id, 'access')}"}
            other_headers = {"Authorization": f"Bearer {make_token(other.id, 'access')}"}
            forbidden = await client.get(f"/api/v1/documents/{doc.id}/quiz", headers=other_headers)
            assert forbidden.status_code == 404
            response = await client.get(f"/api/v1/documents/{doc.id}/quiz", headers=owner_headers)
            assert response.status_code == 200
            library = await client.get("/api/v1/documents", headers=owner_headers)
            assert library.json()[0]["generated_question_count"] == 3
            questions = response.json()["questions"]
            assert [item["has_model_answer"] for item in questions] == [False, True, False]
            assert all("model_answer" not in item for item in questions)
            assert "correct_index" not in questions[0]
            assert "explanation" not in questions[0]
            versions = await client.get(
                f"/api/v1/documents/{doc.id}/quizzes", headers=owner_headers,
            )
            assert [item["id"] for item in versions.json()] == [str(quiz.id)]
            assert (await client.get(
                f"/api/v1/quizzes/{quiz.id}", headers=other_headers,
            )).status_code == 404
            invalid = await client.post(
                f"/api/v1/quizzes/{quiz.id}/attempts",
                json={"answers": []}, headers=owner_headers,
            )
            assert invalid.status_code == 422
            submitted = await client.post(
                f"/api/v1/quizzes/{quiz.id}/attempts",
                json={"answers": [{"question_id": str(question.id), "selected_index": 1}]},
                headers=owner_headers,
            )
            assert submitted.status_code == 201
            assert submitted.json()["score"] == submitted.json()["total"] == 1
            assert submitted.json()["results"][0]["correct_index"] == 1
            assert submitted.json()["results"][1]["model_answer"] == "Sample model answer"
            assert submitted.json()["results"][2]["model_answer"] is None
            history = await client.get(
                f"/api/v1/quizzes/{quiz.id}/attempts", headers=owner_headers,
            )
            assert len(history.json()) == 1
            assert (await client.get(
                f"/api/v1/quizzes/{quiz.id}/attempts", headers=other_headers,
            )).status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
