"""Unit tests for application settings and URL builders."""

from app.core.config import Settings


def test_settings_default_urls() -> None:
    """Verify fallback URL construction when explicit URLs are not provided."""
    settings = Settings(
        database_url=None,
        qdrant_url=None,
        redis_url=None,
        postgres_user="finsight",
        postgres_password="secret",
        postgres_host="db.local",
        postgres_port=5432,
        postgres_db="finsight_db",
        qdrant_host="qdrant.local",
        qdrant_http_port=6333,
        redis_host="redis.local",
        redis_port=6379,
    )
    assert (
        settings.async_database_url
        == "postgresql+asyncpg://finsight:secret@db.local:5432/finsight_db"
    )
    assert settings.resolved_qdrant_url == "http://qdrant.local:6333"
    assert settings.resolved_redis_url == "redis://redis.local:6379/0"

