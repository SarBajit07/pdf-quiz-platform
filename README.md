# PDF Quiz Platform

Implementation of the technical requirements in `pdf-quiz-platform-trd (2).md`.

## Scaffold

- `frontend/` - Next.js, TypeScript, Tailwind CSS
- `backend/` - FastAPI, Pydantic, pytest
- `docker-compose.yml` - PostgreSQL and Redis for local development
- `storage/` - placeholder for local uploaded-file storage

## Prerequisites

- Node.js 20+
- Python 3.12+
- Docker Desktop
- Ollama optional for local LLM generation

## Local setup

1. Copy `.env.example` to `.env`.
2. Start infrastructure:

   ```powershell
   docker compose up -d postgres redis
   ```

3. Start the API:

   ```powershell
   cd backend
   C:/Python313/python.exe -m uvicorn app.main:app --reload --port 8000
   ```

4. Start the frontend:

   ```powershell
   cd frontend
   npm run dev
   ```

- API health: http://localhost:8000/api/v1/health
- API docs: http://localhost:8000/docs
- Frontend: http://localhost:3000

## Database migrations

From the repository root, start PostgreSQL and apply the schema:

```powershell
docker compose up -d postgres
docker compose run --rm --build api uv run alembic upgrade head
```

For the database integration test, point both URLs at a dedicated PostgreSQL
test database that already exists. Run these commands from `backend/` with
the project's development dependencies installed:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://quiz:quiz@localhost:5432/quiz_test'
$env:DATABASE_URL = $env:TEST_DATABASE_URL
python -m alembic upgrade head
python -m pytest
```

## Current milestone

Authentication is available at `/api/v1/auth/register`, `/api/v1/auth/login`,
`/api/v1/auth/refresh`, and `/api/v1/auth/me`. Login returns a 15-minute access
token and a seven-day refresh token. Refresh tokens rotate on use. Set
`JWT_SECRET` to a unique long random value before running outside local development,
and apply migration `0002` before using the auth endpoints.

PDF uploads are available at `POST /api/v1/documents` (Bearer token, multipart
`file` and optional `doc_type=auto|notes|exam`). The API checks MIME, signature,
and the 20 MB file limit and creates a durable PostgreSQL extraction job.
The whole multipart request is limited to 20 MB plus 64 KB of overhead before parsing.
Poll `GET /api/v1/documents/{id}` for `queued`, `processing`, `ready`, or `failed`.
The upload also accepts `num_questions` (5-30, default 10) and `difficulty`
(`easy`, `medium`, or `hard`, default `medium`). Page count is zero until
extraction completes. Invalid PDFs and PDFs over 100 pages fail asynchronously.
The worker stores page text and creates a quiz before setting the document to
`ready`. The document API reports the actual quiz size as
`generated_question_count`. For notes, this can be below the requested
`num_questions` when the model returns too few valid questions.
`needs_confirmation` means automatic type classification was below
70% confidence; call `PATCH /api/v1/documents/{id}/type` with JSON such as
`{"doc_type":"notes"}` or `{"doc_type":"exam"}` to resume. This endpoint
also corrects a type after a completed or failed job and requeues generation.

Start the API and extraction worker with:

```powershell
docker compose up -d --build api worker
```

Apply migrations first. Both services mount the named `upload_data` volume at
`/app/storage`, which survives container replacement. Keep this volume together
with database backups. Removing volumes explicitly removes the uploads.

For native development, run `uv run python -m app.worker` from `backend/` in a
second terminal. Set the same absolute `STORAGE_PATH` and `DATABASE_URL` for both
processes (use a local database hostname rather than Compose's `postgres`).
The worker polls the existing jobs table and reclaims jobs left processing for
15 minutes after a crash. Extraction runs in a disposable subprocess with a
120-second timeout. Temporary failures and timeouts retry up to three times
after 5, 10, and 20 seconds; invalid PDFs fail immediately. Crashed attempts
also count toward the retry limit. Apply migration `0003` for retry scheduling.
Configure `EXTRACTION_TIMEOUT_SECONDS` (maximum 600), `EXTRACTION_MAX_RETRIES`,
and `EXTRACTION_RETRY_SECONDS` to change these defaults.
The worker classifies `auto` uploads, generates notes questions from page-marked
chunks, or extracts exam questions and answer-key entries. Questions are
validated and saved with their source page. Exam answers without a verified key
entry are marked `ai_solved`; disagreement with a key is flagged. Model-generated
answers should be reviewed before relying on them. Scanned PDFs with mostly
empty text fail with a message requesting a text-based PDF; OCR is a later phase.

Set `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` for the primary
OpenAI-compatible provider. The defaults use local Ollama. Optional fallback
providers use `LLM_FALLBACKS` plus `GEMINI_API_KEY`, `GROQ_API_KEY`, or
`OPENROUTER_API_KEY`. Only fallbacks with keys are used. Set rate and concurrency
limits to match your provider. Apply migrations through `0005` before starting the new API
and worker. A `ready` document has a saved quiz and questions.

## Quiz experience

The frontend at `http://localhost:3000` supports account creation, sign-in,
PDF upload, status tracking, type confirmation, quiz taking, results, and
attempt review. It uses `http://localhost:8000/api/v1` by default. Set
`NEXT_PUBLIC_API_URL` before building if the API has another public URL.
Tokens are kept in browser session storage.

The API serves questions without answers at `GET /api/v1/documents/{id}/quiz`.
`POST /api/v1/quizzes/{id}/attempts` scores MCQs. Attempts can be listed and
reviewed at `GET /api/v1/quizzes/{id}/attempts` and
`GET /api/v1/quizzes/{id}/attempts/{attempt_id}`. Non-MCQ exam questions are
shown as flashcards and excluded from the score. Regeneration is available at
`POST /api/v1/quizzes/{id}/regenerate`; previous quizzes and attempts remain
stored. `DELETE /api/v1/documents/{id}` removes the PDF and all derived data.
