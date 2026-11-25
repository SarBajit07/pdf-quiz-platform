from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://quiz:quiz@localhost:5432/quiz"
    redis_url: str = "redis://localhost:6379/0"

    cors_origins: list[str] = ["http://localhost:3000"]

    llm_provider: str = "ollama"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3.5:4b"
    llm_fallbacks: str = "gemini,groq"

    jwt_secret: str = "change-me-in-production-use-a-unique-32-byte-secret"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    storage_backend: str = "local"
    storage_path: str = "./storage"
    max_upload_mb: int = 20
    max_pages: int = 100
    extraction_timeout_seconds: float = Field(default=120, gt=0, le=600)
    extraction_max_retries: int = Field(default=3, ge=0, le=10)
    extraction_retry_seconds: float = Field(default=5, gt=0)


settings = Settings()
