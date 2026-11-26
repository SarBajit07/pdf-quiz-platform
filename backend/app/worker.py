"""Durable extraction and quiz-generation worker."""

import asyncio
import json
import logging
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.database import SessionFactory
from app.generation import GenerationError, classify_document, generate_exam, generate_notes
from app.llm import LLMClient, LLMError, OpenAICompatibleClient
from app.models import Document, DocumentPage, Job, Question, Quiz

logger = logging.getLogger(__name__)


async def _claimed_job(session: AsyncSession, job_id: UUID, attempt: int) -> Job | None:
    job = await session.get(Job, job_id, with_for_update=True)
    if job is None or job.status != "processing" or job.attempts_count != attempt:
        return None
    return job


async def keep_job_alive(
    job_id: UUID, attempt: int, sessions: async_sessionmaker[AsyncSession],
) -> None:
    while True:
        await asyncio.sleep(60)
        async with sessions() as session, session.begin():
            job = await _claimed_job(session, job_id, attempt)
            if job is None:
                return
            job.started_at = datetime.now(UTC)


async def run_extractor(path: str) -> list[str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.extractor", path, str(settings.max_pages),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=settings.extraction_timeout_seconds
        )
    except BaseException:
        # Also clean up the child when the worker task is cancelled.
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # Drain pipes as well as reaping the process; wait() alone can block
        # when a cancelled communicate() left a full stdout pipe.
        await process.communicate()
        raise
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeError) as exc:
        raise OSError("Extraction process exited without a valid result") from exc
    if process.returncode == 2:
        raise ValueError(result["error"])
    if process.returncode != 0:
        raise OSError("Extraction process failed")
    return result["pages"]


async def process_next_job(
    sessions: async_sessionmaker[AsyncSession] = SessionFactory, *, client: LLMClient | None = None,
) -> bool:
    async with sessions() as session, session.begin():
        now = datetime.now(UTC)
        job = await session.scalar(
            select(Job).where(or_(
                (Job.status == "queued") & or_(
                    Job.next_attempt_at.is_(None), Job.next_attempt_at <= now
                ),
                (Job.status == "processing") & (Job.started_at < now - timedelta(minutes=15)),
            )).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1)
        )
        if job is None:
            return False
        document = await session.get(Document, job.document_id)
        if job.attempts_count >= settings.extraction_max_retries + 1:
            job.status = document.status = "failed"
            job.error = document.error_message = "Processing retry limit reached"
            job.finished_at = now
            job.next_attempt_at = None
            return True
        job.status = "processing"
        job.started_at = now
        job.finished_at = job.next_attempt_at = None
        job.attempts_count += 1
        job_id, document_id, attempt = job.id, job.document_id, job.attempts_count
        document.status = "processing"
        document.error_message = None
        path = document.storage_path
        doc_type = document.doc_type
        count = document.num_questions
        difficulty = document.difficulty

    heartbeat = asyncio.create_task(keep_job_alive(job_id, attempt, sessions))
    classification = None
    try:
        async with sessions() as session:
            stored_pages = list(await session.scalars(
                select(DocumentPage).where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_number)
            ))
        pages = [page.text for page in stored_pages] if stored_pages else await run_extractor(path)
        pages = [text.replace("\x00", "") for text in pages]
        if not stored_pages:
            async with sessions() as session, session.begin():
                if await _claimed_job(session, job_id, attempt) is None:
                    return True
                document = await session.get(Document, document_id)
                document.page_count = len(pages)
                session.add_all([
                    DocumentPage(document_id=document_id, page_number=i,
                                 text=text, has_text=bool(text.strip()))
                    for i, text in enumerate(pages, 1)
                ])
        if sum(bool(text.strip()) for text in pages) / len(pages) < 0.4:
            raise GenerationError("Most pages have no extractable text; use a text-based PDF")
        if client is None:
            client = OpenAICompatibleClient()
        if doc_type is None:
            doc_type, confidence = await classify_document(pages, client)
            classification = (doc_type, confidence)
        quiz = None
        if not classification or classification[1] >= 0.7:
            quiz = (await generate_notes(pages, count, difficulty, client) if doc_type == "notes"
                    else await generate_exam(pages, client))
        async with sessions() as session, session.begin():
            job = await _claimed_job(session, job_id, attempt)
            if job is None:
                return True
            document = await session.get(Document, document_id)
            if classification:
                document.doc_type, document.doc_type_confidence = classification
            job.finished_at = datetime.now(UTC)
            job.error = document.error_message = None
            job.status = document.status = "needs_confirmation" if quiz is None else "ready"
            if quiz is not None:
                await session.execute(delete(Quiz).where(Quiz.document_id == document_id))
                saved = Quiz(document_id=document_id, user_id=document.user_id,
                             title=document.filename.rsplit(".", 1)[0],
                             num_questions=len(quiz.questions), difficulty=difficulty,
                             model=quiz.model)
                session.add(saved)
                await session.flush()
                session.add_all([
                    Question(quiz_id=saved.id, position=i, **item)
                    for i, item in enumerate(quiz.questions, 1)
                ])
    except Exception as exc:
        logger.exception("Processing failed for document %s", document_id)
        retryable = not isinstance(exc, (ValueError, IntegrityError, DataError))
        error = (str(exc) if isinstance(exc, ValueError) else
                 "PDF extraction timed out" if isinstance(exc, TimeoutError) else
                 "LLM providers unavailable" if isinstance(exc, LLMError) else
                 "Unable to save processing results" if isinstance(exc, SQLAlchemyError) else
                 "Unable to process PDF")
        # A failed quiz write has rolled back. Record the outcome in a new transaction.
        async with sessions() as session, session.begin():
            job = await _claimed_job(session, job_id, attempt)
            if job is None:
                return True
            document = await session.get(Document, document_id)
            job.finished_at = datetime.now(UTC)
            job.error = document.error_message = error
            job.status = document.status = "failed"
            if classification:
                document.doc_type, document.doc_type_confidence = classification
            if retryable and attempt <= settings.extraction_max_retries:
                job.status = document.status = "queued"
                job.next_attempt_at = job.finished_at + timedelta(
                    seconds=settings.extraction_retry_seconds * 2 ** (attempt - 1)
                )
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    return True


async def main() -> None:
    client = OpenAICompatibleClient()
    while True:
        try:
            if await process_next_job(client=client):
                continue
        except Exception:
            logger.exception("Worker iteration failed")
        await asyncio.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
