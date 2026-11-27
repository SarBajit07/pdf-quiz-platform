from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import router as auth_router
from app.core.config import settings
from app.documents import router as documents_router
from app.quizzes import router as quizzes_router
from app.upload_limit import UploadLimitMiddleware

app = FastAPI(
    title="PDF Quiz Platform API",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(UploadLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(quizzes_router)


@app.exception_handler(HTTPException)
async def api_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail), "code": "http_error"},
        headers=exc.headers,
    )


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pdf-quiz-api"}
