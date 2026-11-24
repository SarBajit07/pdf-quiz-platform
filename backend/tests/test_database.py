import os
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Attempt, Document, Quiz, User


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


async def test_create_and_retrieve_user(sessions) -> None:
    email = f"{uuid4()}@example.test"
    async with sessions.begin() as writer:
        user = User(email=email, password_hash="test-hash")
        writer.add(user)
    async with sessions() as reader:
        stored = await reader.get(User, user.id)
        assert stored is not None
        assert stored.email == email
        assert stored.role == "user"


@pytest.mark.parametrize("record", ["quiz", "attempt"])
async def test_child_must_share_parent_owner(sessions, record) -> None:
    async with sessions() as session:
        owner, other = [
            User(email=f"{uuid4()}@example.test", password_hash="test") for _ in range(2)
        ]
        session.add_all([owner, other])
        await session.flush()
        document = Document(
            user_id=owner.id,
            filename="sample.pdf",
            storage_path="sample.pdf",
            size_bytes=100,
            page_count=1,
            doc_type_source="user",
        )
        session.add(document)
        await session.flush()
        quiz = Quiz(
            document_id=document.id, user_id=owner.id, title="Sample", num_questions=1, model="test"
        )
        if record == "quiz":
            quiz.user_id = other.id
            child = quiz
            constraint = "fk_quiz_document_owner"
        else:
            session.add(quiz)
            await session.flush()
            child = Attempt(quiz_id=quiz.id, user_id=other.id, answers=[], score=0, total=1)
            constraint = "fk_attempt_quiz_owner"
        with pytest.raises(IntegrityError, match=constraint):
            async with session.begin_nested():
                session.add(child)
                await session.flush()
