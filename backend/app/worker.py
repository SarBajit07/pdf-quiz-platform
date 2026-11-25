"""Durable extraction queue using the existing PostgreSQL jobs table."""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select

from app.core.config import settings
from app.database import SessionFactory
from app.models import Document, DocumentPage, Job

logger = logging.getLogger(__name__)


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


async def process_next_job(sessions=SessionFactory) -> bool:
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
            job.error = document.error_message = "Extraction retry limit reached"
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

    error = None
    retryable = False
    try:
        pages = await run_extractor(path)
    except Exception as exc:
        logger.exception("Extraction failed for document %s", document_id)
        retryable = not isinstance(exc, ValueError)
        error = (str(exc) if not retryable else
                 "PDF extraction timed out" if isinstance(exc, TimeoutError) else
                 "Unable to extract PDF text")

    async with sessions() as session, session.begin():
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None or job.attempts_count != attempt or job.status != "processing":
            return True
        document = await session.get(Document, document_id)
        job.finished_at = datetime.now(UTC)
        job.error = document.error_message = error
        job.status = document.status = "failed" if error else "ready"
        if error and retryable and attempt <= settings.extraction_max_retries:
            job.status = document.status = "queued"
            job.next_attempt_at = job.finished_at + timedelta(
                seconds=settings.extraction_retry_seconds * 2 ** (attempt - 1)
            )
        if error is None:
            # PDF character mappings may contain U+0000, which PostgreSQL
            # text columns cannot store. Preserve page boundaries while cleaning.
            pages = [text.replace("\x00", "") for text in pages]
            await session.execute(delete(DocumentPage).where(
                DocumentPage.document_id == document_id
            ))
            document.page_count = len(pages)
            session.add_all([
                DocumentPage(document_id=document_id, page_number=i,
                             text=text, has_text=bool(text.strip()))
                for i, text in enumerate(pages, 1)
            ])
    return True


async def main() -> None:
    while True:
        try:
            if await process_next_job():
                continue
        except Exception:
            logger.exception("Worker iteration failed")
        await asyncio.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
