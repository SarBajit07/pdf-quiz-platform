# Repository Guidelines

## Project structure

The PDF Quiz Platform uses FastAPI and PostgreSQL for the backend and Next.js for the frontend. In `backend/app/`, `main.py` defines the API, `database.py` configures async sessions, and `models.py` defines the SQLAlchemy tables. Keep API request and response schemas separate from database models. Put schema changes in `backend/migrations/versions/` and backend tests in `backend/tests/`.

Next.js routes, layouts, and global styles live in `frontend/src/app/`. Put static assets in `frontend/public/`. Read [the frontend instructions](frontend/AGENTS.md) before editing frontend code.

## Development commands

Copy `.env.example` to `.env` for local configuration. Keep credentials and uploaded files out of Git.

| Directory | Command | Purpose |
| --- | --- | --- |
| Repository root | `docker compose up -d postgres redis` | Start local infrastructure. |
| Repository root | `docker compose run --rm --build api uv run alembic upgrade head` | Apply migrations to the Compose database. |
| `backend/` | `uv run uvicorn app.main:app --reload --port 8000` | Run the API locally with reload enabled. |
| `backend/` | `uv run ruff check .` | Lint Python code, including migrations. |
| `frontend/` | `npm ci`, then `npm run dev` | Install locked dependencies and start the development server. |
| `frontend/` | `npm run lint` and `npm run build` | Run ESLint and build the production app. |

## Style and testing

Use four spaces in Python, type annotations, snake_case functions, and PascalCase model classes. Ruff uses a 100-character line limit. Follow the existing two-space indentation in TypeScript and PascalCase names for React components.

Name pytest files and functions `test_*`. For database changes, create a dedicated PostgreSQL test database and point both `DATABASE_URL` and `TEST_DATABASE_URL` at it. From `backend/`, run `uv run alembic upgrade head`, then `uv run pytest`. Database tests skip when `TEST_DATABASE_URL` is unset. The requirements document targets at least 80% coverage for services.

## Commits and pull requests

Use short, imperative commit subjects. History includes plain subjects and prefixes such as `fix:`. Keep model changes, their migration, and regression tests together. In a pull request, describe the behavior change, report the checks run, and link a related issue when available. Include a screenshot for visible UI changes.
