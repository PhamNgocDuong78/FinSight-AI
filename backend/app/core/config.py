"""Centralized application configuration powered by Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 1. Application Settings
    app_name: str = Field(default="FinSight AI")
    app_env: Literal["development", "staging", "production", "test"] = Field(
        default="development"
    )
    app_debug: bool = Field(default=True)
    api_v1_prefix: str = Field(default="/api/v1")
    secret_key: str = Field(default="change-me-in-production-use-a-long-random-string")
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # 2. PostgreSQL 16 (Async SQLAlchemy)
    postgres_user: str = Field(default="finsight")
    postgres_password: str = Field(default="finsight_secret")
    postgres_db: str = Field(default="finsight_db")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    database_url: str | None = Field(default=None)

    # 3. Qdrant Vector Database
    qdrant_host: str = Field(default="localhost")
    qdrant_http_port: int = Field(default=6333)
    qdrant_grpc_port: int = Field(default=6334)
    qdrant_url: str | None = Field(default=None)
    qdrant_api_key: str | None = Field(default=None)
    qdrant_collection_name: str = Field(default="finsight_child_chunks")

    # 4. Redis (Cache & Queue)
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_url: str | None = Field(default=None)
    semantic_cache_ttl_seconds: int = Field(default=3600)

    # 5. LLM & Embedding Providers (Pluggable Adapter Layer)
    llm_provider: Literal["gemini", "openai", "ollama"] = Field(default="gemini")
    gemini_api_key: str = Field(default="")
    gemini_fast_model: str = Field(default="gemini-2.5-flash")
    gemini_reasoning_model: str = Field(default="gemini-2.5-pro")
    gemini_embedding_model: str = Field(default="gemini-embedding-001")
    embedding_dimension: int = Field(default=768)
    openai_api_key: str = Field(default="")
    ollama_base_url: str = Field(default="http://localhost:11434")

    # 6. Retrieval & Agentic Workflow Configuration
    hybrid_search_top_k: int = Field(default=20)
    rerank_top_k: int = Field(default=5)
    max_agent_retries: int = Field(default=2)
    parent_chunk_size: int = Field(default=1200)
    child_chunk_size: int = Field(default=300)

    # 7. Storage Paths
    raw_pdf_dir: Path = Field(default=PROJECT_ROOT / "data" / "raw_pdfs")
    processed_md_dir: Path = Field(default=PROJECT_ROOT / "data" / "processed_md")
    eval_dataset_dir: Path = Field(default=PROJECT_ROOT / "data" / "eval_datasets")

    @field_validator("raw_pdf_dir", "processed_md_dir", "eval_dataset_dir", mode="after")
    @classmethod
    def resolve_storage_path(cls, value: Path) -> Path:
        """Anchor relative storage paths to PROJECT_ROOT."""
        if not value.is_absolute():
            return (PROJECT_ROOT / value).resolve()
        return value

    # 8. Observability
    langfuse_enabled: bool = Field(default=False)
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")
    langfuse_host: str = Field(default="https://cloud.langfuse.com")

    @property
    def async_database_url(self) -> str:
        """Return the asyncpg SQLAlchemy database URL."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def resolved_qdrant_url(self) -> str:
        """Return the HTTP URL for Qdrant."""
        if self.qdrant_url:
            return self.qdrant_url
        return f"http://{self.qdrant_host}:{self.qdrant_http_port}"

    @property
    def resolved_redis_url(self) -> str:
        """Return the Redis connection URL."""
        if self.redis_url:
            return self.redis_url
        return f"redis://{self.redis_host}:{self.redis_port}/0"


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()

