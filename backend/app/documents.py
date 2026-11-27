import io
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pdfplumber
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import Select, delete, select

from app.auth import Session, get_current_user
from app.core.config import settings
from app.models import Document, Job, PendingFileDeletion, Quiz, User
from app.schemas.documents import DocumentResponse

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
CurrentUser = Annotated[User, Depends(get_current_user)]


class DocumentTypeUpdate(BaseModel):
    doc_type: Literal["notes", "exam"]


def extract_pages(data: bytes) -> list[str]:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not pdf.pages or len(pdf.pages) > settings.max_pages:
                raise ValueError("PDF page count is outside the allowed range")
            return [page.extract_text() or "" for page in pdf.pages]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid or unreadable PDF") from exc


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    session: Session,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[Literal["notes", "exam", "auto"], Form()] = "auto",
    num_questions: Annotated[int, Form(ge=5, le=30)] = 10,
    difficulty: Annotated[Literal["easy", "medium", "hard"], Form()] = "medium",
) -> Document:
    if file.content_type != "application/pdf":
        raise HTTPException(400, "File must have application/pdf content type")
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "PDF exceeds the upload size limit")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "Invalid PDF signature")
    directory = Path(settings.storage_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid4()}.pdf"
    await run_in_threadpool(path.write_bytes, data)
    document = Document(
        user_id=user.id, filename=Path((file.filename or "document.pdf").replace("\\", "/")).name,
        storage_path=str(path.resolve()), size_bytes=len(data), page_count=0,
        doc_type=None if doc_type == "auto" else doc_type,
        doc_type_source="auto" if doc_type == "auto" else "user", status="queued",
        num_questions=num_questions, difficulty=difficulty,
    )
    try:
        session.add(document)
        await session.flush()
        session.add(Job(document_id=document.id))
        await session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return document


def _documents_with_question_count(user_id: UUID) -> Select[tuple[Document, int | None]]:
    question_count = (
        select(Quiz.num_questions)
        .where(Quiz.document_id == Document.id)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    return select(Document, question_count).where(Document.user_id == user_id)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(session: Session, user: CurrentUser) -> list[DocumentResponse]:
    result = await session.execute(
        _documents_with_question_count(user.id).order_by(Document.created_at.desc())
    )
    return [
        DocumentResponse.model_validate(document).model_copy(
            update={"generated_question_count": question_count},
        )
        for document, question_count in result
    ]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, session: Session, user: CurrentUser) -> DocumentResponse:
    result = await session.execute(
        _documents_with_question_count(user.id).where(Document.id == document_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(404, "Document not found")
    document, question_count = row
    return DocumentResponse.model_validate(document).model_copy(
        update={"generated_question_count": question_count},
    )


@router.patch("/{document_id}/type", response_model=DocumentResponse)
async def update_document_type(
    document_id: UUID, update: DocumentTypeUpdate, session: Session, user: CurrentUser,
) -> Document:
    # The worker acquires the job lock before writing to the document.
    job = await session.scalar(
        select(Job).join(Document, Document.id == Job.document_id)
        .where(Job.document_id == document_id, Document.user_id == user.id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1).with_for_update(of=Job)
    )
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
        .with_for_update()
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.status == "processing":
        raise HTTPException(409, "Wait for processing to finish before changing type")
    if job is None:
        raise HTTPException(409, "Document has no processing job")
    document.doc_type = update.doc_type
    document.doc_type_source = "user"
    document.doc_type_confidence = None
    document.status = job.status = "queued"
    document.error_message = job.error = None
    job.attempts_count = 0
    job.next_attempt_at = job.started_at = job.finished_at = None
    await session.commit()
    return document


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: UUID, session: Session, user: CurrentUser) -> None:
    await session.execute(
        select(Job.id).join(Document, Document.id == Job.document_id)
        .where(Document.id == document_id, Document.user_id == user.id)
        .order_by(Job.created_at.desc(), Job.id.desc()).with_for_update(of=Job)
    )
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
        .with_for_update()
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.status == "processing":
        raise HTTPException(409, "Wait for processing to finish before deleting")
    path = Path(document.storage_path)
    if path.parent.resolve() != Path(settings.storage_path).resolve():
        raise HTTPException(409, "PDF is outside the configured storage directory")
    session.add(PendingFileDeletion(document_id=document.id, storage_path=str(path)))
    await session.execute(delete(Document).where(Document.id == document_id))
    await session.commit()
    # The worker retries if the process exits or storage is temporarily unavailable.
    try:
        await run_in_threadpool(path.unlink, missing_ok=True)
    except OSError:
        return
    await session.execute(delete(PendingFileDeletion).where(
        PendingFileDeletion.document_id == document_id
    ))
    await session.commit()
