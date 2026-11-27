from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import Session, get_current_user
from app.models import Attempt, Document, Job, Question, Quiz, User

router = APIRouter(prefix="/api/v1", tags=["quizzes"])
CurrentUser = Annotated[User, Depends(get_current_user)]


class PublicQuestion(BaseModel):
    id: UUID
    position: int
    text: str
    question_type: str
    options: list[str] | None
    source_page: int
    answer_source: str
    flagged: bool
    has_model_answer: bool


class QuizResponse(BaseModel):
    id: UUID
    document_id: UUID
    title: str
    difficulty: str | None
    created_at: datetime
    questions: list[PublicQuestion]


class QuizSummary(BaseModel):
    id: UUID
    title: str
    num_questions: int
    created_at: datetime


class AnswerInput(BaseModel):
    question_id: UUID
    selected_index: int = Field(ge=0, le=3)


class AttemptInput(BaseModel):
    answers: list[AnswerInput]


class QuestionResult(BaseModel):
    question_id: UUID
    correct: bool | None
    selected_index: int | None
    correct_index: int | None
    model_answer: str | None
    explanation: str | None
    source_page: int
    answer_source: str
    flagged: bool


class AttemptResponse(BaseModel):
    id: UUID
    quiz_id: UUID
    score: int
    total: int
    created_at: datetime
    results: list[QuestionResult]


class AttemptSummary(BaseModel):
    id: UUID
    score: int
    total: int
    created_at: datetime


@router.post("/quizzes/{quiz_id}/regenerate", status_code=202)
async def regenerate_quiz(
    quiz_id: UUID, session: Session, user: CurrentUser,
) -> dict[str, str]:
    quiz = await owned_quiz(quiz_id, session, user)
    job = await session.scalar(
        select(Job).where(Job.document_id == quiz.document_id)
        .order_by(Job.created_at.desc(), Job.id.desc()).limit(1).with_for_update()
    )
    document = await session.scalar(
        select(Document).where(Document.id == quiz.document_id, Document.user_id == user.id)
        .with_for_update()
    )
    if document is None or job is None:
        raise HTTPException(404, "Document not found")
    if document.status in {"queued", "processing", "needs_confirmation"}:
        raise HTTPException(409, f"Document is {document.status}")
    document.status = job.status = "queued"
    document.error_message = job.error = None
    job.attempts_count = 0
    job.next_attempt_at = job.started_at = job.finished_at = None
    await session.commit()
    return {"document_id": str(document.id), "status": "queued"}


async def owned_quiz(
    quiz_id: UUID, session: Session, user: CurrentUser, *, lock_for_attempt: bool = False,
) -> Quiz:
    query = select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == user.id)
    if lock_for_attempt:
        query = query.with_for_update(read=True, key_share=True)
    quiz = await session.scalar(query)
    if quiz is None:
        raise HTTPException(404, "Quiz not found")
    return quiz


async def public_quiz(quiz: Quiz, session: Session) -> QuizResponse:
    questions = await session.scalars(
        select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
    )
    return QuizResponse(
        id=quiz.id, document_id=quiz.document_id, title=quiz.title,
        difficulty=quiz.difficulty, created_at=quiz.created_at,
        questions=[
            PublicQuestion(
                id=q.id, position=q.position, text=q.text, question_type=q.question_type,
                options=q.options, source_page=q.source_page, answer_source=q.answer_source,
                flagged=q.flagged, has_model_answer=bool(q.model_answer and q.model_answer.strip()),
            )
            for q in questions
        ],
    )


@router.get("/documents/{document_id}/quizzes", response_model=list[QuizSummary])
async def list_document_quizzes(
    document_id: UUID, session: Session, user: CurrentUser,
) -> list[QuizSummary]:
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    quizzes = await session.scalars(
        select(Quiz).where(Quiz.document_id == document_id, Quiz.user_id == user.id)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
    )
    return [QuizSummary.model_validate(q, from_attributes=True) for q in quizzes]


@router.get("/quizzes/{quiz_id}", response_model=QuizResponse)
async def get_quiz(quiz_id: UUID, session: Session, user: CurrentUser) -> QuizResponse:
    return await public_quiz(await owned_quiz(quiz_id, session, user), session)


@router.get("/documents/{document_id}/quiz", response_model=QuizResponse)
async def get_document_quiz(document_id: UUID, session: Session, user: CurrentUser) -> QuizResponse:
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    quiz = await session.scalar(
        select(Quiz).where(Quiz.document_id == document_id, Quiz.user_id == user.id)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc()).limit(1)
    )
    if quiz is None:
        if document.status != "ready":
            raise HTTPException(409, f"Document is {document.status}")
        raise HTTPException(409, "Quiz is not available yet")
    return await public_quiz(quiz, session)


def attempt_results(questions: list[Question], answers: dict[UUID, int]) -> list[QuestionResult]:
    return [
        QuestionResult(
            question_id=q.id,
            correct=(answers.get(q.id) == q.correct_index) if q.question_type == "mcq" else None,
            selected_index=answers.get(q.id),
            correct_index=q.correct_index,
            model_answer=q.model_answer,
            explanation=q.explanation,
            source_page=q.source_page,
            answer_source=q.answer_source,
            flagged=q.flagged,
        ) for q in questions
    ]


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptResponse, status_code=201)
async def submit_attempt(
    quiz_id: UUID, body: AttemptInput, session: Session, user: CurrentUser,
) -> AttemptResponse:
    quiz = await owned_quiz(quiz_id, session, user, lock_for_attempt=True)
    questions = list(await session.scalars(
        select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
    ))
    mcqs = {q.id: q for q in questions if q.question_type == "mcq"}
    answers = {answer.question_id: answer.selected_index for answer in body.answers}
    if len(answers) != len(body.answers) or set(answers) != set(mcqs):
        raise HTTPException(422, "Submit exactly one answer for every multiple-choice question")
    score = sum(answers[q.id] == q.correct_index for q in mcqs.values())
    attempt = Attempt(
        quiz_id=quiz.id, user_id=user.id,
        answers=[answer.model_dump(mode="json") for answer in body.answers],
        score=score, total=len(mcqs),
    )
    session.add(attempt)
    await session.flush()
    response = AttemptResponse(
        id=attempt.id, quiz_id=quiz.id, score=score, total=len(mcqs),
        created_at=attempt.created_at, results=attempt_results(questions, answers),
    )
    await session.commit()
    return response


@router.get("/quizzes/{quiz_id}/attempts", response_model=list[AttemptSummary])
async def list_attempts(
    quiz_id: UUID, session: Session, user: CurrentUser,
) -> list[AttemptSummary]:
    await owned_quiz(quiz_id, session, user)
    attempts = await session.scalars(
        select(Attempt).where(Attempt.quiz_id == quiz_id, Attempt.user_id == user.id)
        .order_by(Attempt.created_at.desc(), Attempt.id.desc())
    )
    return [AttemptSummary.model_validate(a, from_attributes=True) for a in attempts]


@router.get("/quizzes/{quiz_id}/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(
    quiz_id: UUID, attempt_id: UUID, session: Session, user: CurrentUser,
) -> AttemptResponse:
    quiz = await owned_quiz(quiz_id, session, user)
    attempt = await session.scalar(
        select(Attempt).where(
            Attempt.id == attempt_id, Attempt.quiz_id == quiz.id, Attempt.user_id == user.id,
        )
    )
    if attempt is None:
        raise HTTPException(404, "Attempt not found")
    questions = list(await session.scalars(
        select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
    ))
    answers = {UUID(a["question_id"]): a["selected_index"] for a in attempt.answers}
    return AttemptResponse(
        id=attempt.id, quiz_id=quiz.id, score=attempt.score, total=attempt.total,
        created_at=attempt.created_at, results=attempt_results(questions, answers),
    )
