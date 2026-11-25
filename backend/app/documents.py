import io
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pdfplumber
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.auth import Session, get_current_user
from app.core.config import settings
from app.models import Document, Job, User
from app.schemas.documents import DocumentResponse

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
CurrentUser = Annotated[User, Depends(get_current_user)]


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


@router.get("", response_model=list[DocumentResponse])
async def list_documents(session: Session, user: CurrentUser) -> list[Document]:
    result = await session.scalars(
        select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
    )
    return list(result)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, session: Session, user: CurrentUser) -> Document:
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user.id)
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    return document
