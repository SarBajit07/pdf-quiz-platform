from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=sa.text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class User(Record, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    role: Mapped[str] = mapped_column(server_default="user")
    daily_quota: Mapped[int | None]
    __table_args__ = (sa.CheckConstraint("role IN ('user','admin')", name="ck_user_role"),)


class RefreshToken(Record, Base):
    __tablename__ = "refresh_tokens"
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Document(Record, Base):
    __tablename__ = "documents"
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str]
    storage_path: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    page_count: Mapped[int]
    doc_type: Mapped[str | None]
    doc_type_source: Mapped[str]
    doc_type_confidence: Mapped[float | None]
    status: Mapped[str] = mapped_column(server_default="queued")
    error_message: Mapped[str | None]
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id"),
        sa.Index("ix_documents_user_created", "user_id", sa.desc("created_at")),
        sa.CheckConstraint("doc_type IN ('notes','exam')", name="ck_document_type"),
        sa.CheckConstraint("doc_type_source IN ('user','auto')", name="ck_document_type_source"),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_document_status"
        ),
    )


class DocumentPage(Record, Base):
    __tablename__ = "document_pages"
    document_id: Mapped[UUID] = mapped_column(sa.ForeignKey("documents.id", ondelete="CASCADE"))
    page_number: Mapped[int]
    text: Mapped[str]
    has_text: Mapped[bool]
    __table_args__ = (sa.UniqueConstraint("document_id", "page_number"),)


class Quiz(Record, Base):
    __tablename__ = "quizzes"
    document_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str]
    num_questions: Mapped[int]
    difficulty: Mapped[str | None]
    model: Mapped[str]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    __table_args__ = (
        sa.UniqueConstraint("id", "user_id"),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_quiz_document_owner",
            ondelete="CASCADE",
        ),
    )


class Question(Record, Base):
    __tablename__ = "questions"
    quiz_id: Mapped[UUID] = mapped_column(sa.ForeignKey("quizzes.id", ondelete="CASCADE"))
    position: Mapped[int]
    question_type: Mapped[str]
    origin: Mapped[str]
    answer_source: Mapped[str]
    flagged: Mapped[bool] = mapped_column(server_default=sa.false())
    text: Mapped[str]
    options: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    correct_index: Mapped[int | None]
    model_answer: Mapped[str | None]
    explanation: Mapped[str | None]
    source_page: Mapped[int]
    question_hash: Mapped[str]
    __table_args__ = (
        sa.UniqueConstraint("quiz_id", "question_hash"),
        sa.UniqueConstraint("quiz_id", "position"),
        sa.CheckConstraint(
            "question_type IN ('mcq','short','long','numerical')", name="ck_question_type"
        ),
        sa.CheckConstraint("origin IN ('generated','extracted')", name="ck_question_origin"),
        sa.CheckConstraint(
            "answer_source IN ('generated','answer_key','ai_solved','none')",
            name="ck_answer_source",
        ),
        sa.CheckConstraint(
            "question_type <> 'mcq' OR (correct_index IS NOT NULL "
            "AND correct_index BETWEEN 0 AND 3 AND options IS NOT NULL "
            "AND jsonb_typeof(options) = 'array' AND jsonb_array_length(options) = 4)",
            name="ck_mcq_choices",
        ),
    )


class Attempt(Record, Base):
    __tablename__ = "attempts"
    quiz_id: Mapped[UUID]
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    answers: Mapped[list[dict]] = mapped_column(JSONB)
    score: Mapped[int]
    total: Mapped[int]
    __table_args__ = (
        sa.Index("ix_attempts_quiz_user", "quiz_id", "user_id"),
        sa.ForeignKeyConstraint(
            ["quiz_id", "user_id"],
            ["quizzes.id", "quizzes.user_id"],
            name="fk_attempt_quiz_owner",
            ondelete="CASCADE",
        ),
    )


class Job(Record, Base):
    __tablename__ = "jobs"
    document_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(server_default="queued")
    attempts_count: Mapped[int] = mapped_column(server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None]
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_job_status"
        ),
    )
