"""Tests for configuration."""

from __future__ import annotations

import pytest

from src.config import (
    APISettings,
    AuthSettings,
    CacheSettings,
    DatabaseSettings,
    LoggingSettings,
    PresentationSettings,
    RedisSettings,
    Settings,
    StorageSettings,
)

# Environment variables that pydantic-settings picks up for each settings class.
# These are cleared by the fixtures below so default-value tests aren't polluted
# by the runner's environment (e.g. a CI runner setting DATABASE_URL).
_DATABASE_ENV_VARS = (
    # DATABASE_URL deliberately excluded: w2v_common >= 0.15.0 removed the
    # localhost default, so clearing it would ValidationError every test.
    "DATABASE_POOL_SIZE",
    "DATABASE_POOL_MIN_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT",
    "DATABASE_POOL_RECYCLE",
    "DATABASE_ECHO",
)
_REDIS_ENV_VARS = (
    # REDIS_URL deliberately excluded: w2v_common >= 0.15.0 removed the
    # localhost default, so clearing it would ValidationError every test.
    "REDIS_MAX_CONNECTIONS",
    "REDIS_SOCKET_TIMEOUT",
    "REDIS_SOCKET_CONNECT_TIMEOUT",
    "REDIS_DECODE_RESPONSES",
)
_API_ENV_VARS = (
    "API_HOST",
    "API_PORT",
    "API_CORS_ORIGINS",
    "API_CORS_ALLOW_CREDENTIALS",
    "API_CORS_ALLOW_METHODS",
    "API_CORS_ALLOW_HEADERS",
    "API_DEBUG",
    "API_RELOAD",
)
_AUTH_ENV_VARS = (
    "AUTH_ENABLED",
    "AUTH_SSL_VERIFY",
    "AUTH_CACHE_MAXSIZE",
    "AUTH_CACHE_TTL",
    "AUTH_INTROSPECT_TIMEOUT",
)
_STORAGE_ENV_VARS = (
    "STORAGE_BACKEND",
    "STORAGE_LOCAL_PATH",
    # STORAGE_MINIO_ENDPOINT deliberately excluded: w2v_common >= 0.15.0 removed
    # the localhost default, so clearing it would ValidationError every test.
    "STORAGE_MINIO_EXTERNAL_ENDPOINT",
    "STORAGE_MINIO_ACCESS_KEY",
    "STORAGE_MINIO_SECRET_KEY",
    "STORAGE_MINIO_BUCKET",
    "STORAGE_MINIO_SECURE",
    "STORAGE_PRESIGN_EXPIRY_SECONDS",
)
_PRESENTATION_ENV_VARS = (
    "PRESENTATION_DEFAULT_THEME",
    "PRESENTATION_DEFAULT_ASPECT_RATIO",
    "PRESENTATION_MAX_SLIDES_PER_DECK",
    "PRESENTATION_NOTES_ENABLED",
)
_CACHE_ENV_VARS = (
    "CACHE_ENABLED",
    "CACHE_KEY_PREFIX",
    "CACHE_DEFAULT_TTL",
    "CACHE_DECK_TTL",
    "CACHE_JOB_TTL",
    "CACHE_JOB_MAXSIZE",
    "CACHE_LICENSE_TTL",
    "CACHE_LICENSE_MAXSIZE",
)
_LOGGING_ENV_VARS = (
    "LOG_LEVEL",
    "LOG_FORMAT",
    "LOG_DATE_FORMAT",
    "LOG_JSON_LOGS",
)
_MAIN_ENV_VARS = (
    "APP_NAME",
    "APP_VERSION",
    "ENVIRONMENT",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Delete environment variables so they don't pollute default-value tests."""
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_database_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test DatabaseSettings non-url fields default correctly when env not set."""
    _clear_env(monkeypatch, *_DATABASE_ENV_VARS)
    db = DatabaseSettings(url="postgresql+psycopg://test:test@localhost:5432/test")
    assert db.url.startswith("postgresql+psycopg://")
    assert db.pool_size == 10


def test_redis_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test RedisSettings non-url fields default correctly when env not set."""
    _clear_env(monkeypatch, *_REDIS_ENV_VARS)
    redis = RedisSettings(url="redis://localhost:6379/0")
    assert redis.url.startswith("redis://")
    assert redis.max_connections == 10


def test_api_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test APISettings has correct defaults from w2v_common."""
    _clear_env(monkeypatch, *_API_ENV_VARS)
    api = APISettings()
    # Default port comes from w2v_common (8000); overridden via API_PORT env at deploy
    assert api.host == "0.0.0.0"


def test_auth_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test AuthSettings has correct defaults."""
    _clear_env(monkeypatch, *_AUTH_ENV_VARS)
    auth = AuthSettings()
    assert auth.enabled is True
    # w2v-common 0.67.0 DELETED ``default_tenant_id``. It used to hold the nil
    # uuid as a "fail-safe" substitute for an absent tenant -- a valid uuid that
    # passed every type check while silently mis-attributing every row and every
    # cost event to ``default-anon``. There is deliberately NO replacement: an
    # absent tenant must fail loud (slide_service answers 401, see
    # ``src.api.routes._get_tenant_id``). This asserts the attribute is GONE so a
    # future w2v-common cannot quietly reintroduce a fallback tenant here.
    assert not hasattr(auth, "default_tenant_id")


def test_storage_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test StorageSettings has correct defaults from w2v_common."""
    _clear_env(monkeypatch, *_STORAGE_ENV_VARS)
    storage = StorageSettings()
    assert storage.backend == "local"
    # Default bucket comes from w2v_common; service-specific bucket set via STORAGE_MINIO_BUCKET env
    assert storage.presign_expiry_seconds == 3600


def test_presentation_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test PresentationSettings has correct defaults."""
    _clear_env(monkeypatch, *_PRESENTATION_ENV_VARS)
    pres = PresentationSettings()
    assert pres.default_theme == "modern"
    assert pres.default_aspect_ratio == "16:9"
    assert pres.max_slides_per_deck == 50
    assert pres.notes_enabled is True


def test_cache_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CacheSettings has correct defaults."""
    _clear_env(monkeypatch, *_CACHE_ENV_VARS)
    cache = CacheSettings()
    assert cache.enabled is True
    assert cache.deck_ttl == 3600


def test_logging_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test LoggingSettings has correct defaults."""
    _clear_env(monkeypatch, *_LOGGING_ENV_VARS)
    log = LoggingSettings()
    assert log.level == "INFO"
    assert log.json_logs is False


def test_settings_hierarchy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that Settings composes all sub-configs."""
    _clear_env(
        monkeypatch,
        *_MAIN_ENV_VARS,
        *_DATABASE_ENV_VARS,
        *_REDIS_ENV_VARS,
        *_API_ENV_VARS,
        *_AUTH_ENV_VARS,
        *_STORAGE_ENV_VARS,
        *_PRESENTATION_ENV_VARS,
        *_CACHE_ENV_VARS,
        *_LOGGING_ENV_VARS,
    )
    settings = Settings()
    assert settings.app_name == "Slide Service"
    assert settings.app_version == "0.1.0"
    assert settings.environment == "development"
    assert isinstance(settings.database, DatabaseSettings)
    assert isinstance(settings.redis, RedisSettings)
    assert isinstance(settings.api, APISettings)
    assert isinstance(settings.presentation, PresentationSettings)


def test_settings_helper_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Settings helper methods."""
    _clear_env(
        monkeypatch,
        *_MAIN_ENV_VARS,
        *_DATABASE_ENV_VARS,
        *_REDIS_ENV_VARS,
        *_API_ENV_VARS,
        *_AUTH_ENV_VARS,
        *_STORAGE_ENV_VARS,
        *_PRESENTATION_ENV_VARS,
        *_CACHE_ENV_VARS,
        *_LOGGING_ENV_VARS,
    )
    settings = Settings()
    assert settings.is_development() is True
    assert settings.is_production() is False
    assert settings.get_database_url().startswith("postgresql+psycopg://")
    assert settings.get_redis_url().startswith("redis://")


def test_api_cors_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CORS origins accepts a list of origins."""
    _clear_env(monkeypatch, *_API_ENV_VARS)
    api = APISettings(cors_origins=["http://a.com", "http://b.com"])
    assert api.cors_origins == ["http://a.com", "http://b.com"]
