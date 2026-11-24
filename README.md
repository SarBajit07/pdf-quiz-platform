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

## Next milestone

The next foundation work is registration, login, and JWT authentication.
