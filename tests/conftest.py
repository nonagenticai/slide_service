"""Shared test fixtures for slide_service tests."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment variables before importing any app code.
# Port 5473 (the slide_service_test_postgres compose service), NOT 5432: a 5432
# default resolves to whatever postgres the developer happens to be running, which
# answers the TCP connect and THEN fails auth — disguising a config error as a
# connection error. User/db are ci_test/slide_service_ci so that conftest,
# scripts/ci-local.sh and docker-compose.yml all agree on the same server.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://ci_test:ci_test_password_secure@127.0.0.1:5473/slide_service_ci",  # pragma: allowlist secret
)
# w2v_common >= 0.15.0 dropped the localhost defaults on RedisSettings.url and
# StorageSettings.minio_endpoint; seed module-level so Settings() instantiation
# during pytest collection doesn't fail. The autouse _clear_settings fixture
# re-applies them after its env-clearing sweep.
#
# Port 6396, NOT 6379 — same reasoning as the 5473 DATABASE_URL default above:
# 6396 is what `docker-compose.yml` publishes for the `redis` service (and what
# CLAUDE.md documents), while nothing in this repo has ever published 6379. A
# 6379 default therefore names a port that exists on no developer machine, so
# the local default silently disagreed with compose and with the docs.
#
# REDIS_HOST / REDIS_PORT come first because that is what the gitops
# `python-service` archetype exports for the ephemeral `has_redis` sidecar it
# starts per job, on a RANDOM host port (`docker run -p 127.0.0.1::6379`). This
# module never read them, and `_clear_settings` below re-set REDIS_URL
# unconditionally, so the sidecar was unreachable in CI too and `has_redis:
# true` bought nothing. Reading them here is what makes that input real.
#
# NB the suite does not currently require a reachable redis — caching and the
# job lock are both fail-open — so this is a correctness fix to the default, not
# a fix to a failing gate. It must stay that way: do not add a skip-if-absent
# guard, and do not let a test start depending on redis being up locally.
_REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
_REDIS_PORT = os.environ.get("REDIS_PORT", "6396")
os.environ.setdefault("REDIS_URL", f"redis://{_REDIS_HOST}:{_REDIS_PORT}/0")
os.environ.setdefault("STORAGE_MINIO_ENDPOINT", "localhost:9000")

# Capture the test DB URL BEFORE the autouse _clear_settings fixture
# monkeypatches DATABASE_URL to its in-test default. CI sets DATABASE_URL
# to its slide_service_ci postgres; locally falls back to the default above
# (developer must run a local postgres to execute db_engine tests).
_TEST_DATABASE_URL = os.environ["DATABASE_URL"]
# Same capture-before-sweep contract for redis: _clear_settings deletes every
# REDIS_* var and must restore the URL resolved above, not a second hardcoded
# literal that can drift away from it (which is exactly how the 6379/6396 split
# survived).
_TEST_REDIS_URL = os.environ["REDIS_URL"]


# ---------------------------------------------------------------------------
# Settings cache management
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    """Remove CI env vars that interfere with config-default tests."""
    for key in list(os.environ):
        if any(
            key.startswith(p)
            for p in (
                "DATABASE_",
                "AUTH_",
                "REDIS_",
                "API_",
                "STORAGE_",
                "LOG_",
                "CACHE_",
                "TRACING_",
                "FFMPEG_",
                "PRESENTATION_",
                "WORKER_MANAGEMENT_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    # Restore w2v_common 0.15.0 fail-fast fields with placeholders so
    # config-default tests work without hitting ValidationError on missing url
    # / minio_endpoint env.
    monkeypatch.setenv("DATABASE_URL", _TEST_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", _TEST_REDIS_URL)
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "localhost:9000")


@pytest.fixture
def sample_brand() -> dict:
    """Sample brand configuration for tests."""
    return {
        "primary_color": "#1a73e8",
        "secondary_color": "#34a853",
        "font_family": "Arial",
        "logo_url": None,
        "theme": "modern",
    }


@pytest.fixture
def sample_deck_request(sample_brand: dict) -> dict:
    """Sample deck creation request payload."""
    return {
        "title": "Test Deck",
        "template_ids": [],
        "slides": [
            {
                "template_id": "test-template-id",
                "content": {
                    "title": "Test Slide",
                    "subtitle": "Test Subtitle",
                },
            }
        ],
        "brand": sample_brand,
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {
            "target_duration_seconds": 120.0,
            "default_slide_duration_seconds": 15.0,
        },
    }


@pytest.fixture
def sample_template_data() -> dict:
    """Sample template creation data."""
    return {
        "name": "test_template",
        "display_name": "Test Template",
        "category": "narrative",
        "layout_type": "title",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "subtitle": {"type": "text", "max_length": 120, "required": False},
        },
        "styling_rules": {
            "title_font_size": 40,
            "subtitle_font_size": 24,
            "layout": "centered",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 8.0,
    }


# ---------------------------------------------------------------------------
# Real database fixtures (postgres+psycopg, TRUNCATE-based isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine():
    """Per-test async postgres engine with TRUNCATE-based isolation.

    Connects to _TEST_DATABASE_URL captured at module load (CI postgres in
    CI, localhost default locally). Runs Base.metadata.create_all (idempotent)
    then TRUNCATEs every model table so each test starts from a clean slate.
    """
    from src.database.models import Base

    engine = create_async_engine(_TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if Base.metadata.sorted_tables:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session_factory(db_engine):
    """Real async_sessionmaker for slide_service tests."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db_session(db_session_factory):
    """Real AsyncSession for a single test."""
    async with db_session_factory() as session:
        yield session
