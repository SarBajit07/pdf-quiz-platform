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

## Next milestone

The foundation milestone starts with database models, Alembic migrations, registration/login, and JWT authentication.
