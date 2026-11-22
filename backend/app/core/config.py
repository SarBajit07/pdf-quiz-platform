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

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"

    storage_backend: str = "local"
    storage_path: str = "./storage"
    max_upload_mb: int = 20
    max_pages: int = 100


settings = Settings()
