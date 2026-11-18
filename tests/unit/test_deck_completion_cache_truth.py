"""The status cache may never claim a completion the database did not record.

Two defects live here, both of the fleet's #1 class -- a fail-open on a
measurement gap, amplified by a cache:

1. ``_process_deck`` wrote ``{"status": "completed"}`` into Redis
   UNCONDITIONALLY, including down the ``except`` path where the DB completion
   write had just failed. The row stays non-terminal on purpose ("recoverable by
   the reaper / a re-poll"), but ``get_deck_status`` PREFERS the cache, so the
   poll answered a terminal ``completed`` for the full 300s
   ``job_status_ttl("completed")`` while every artifact route -- which reads
   ``row.status`` through ``_not_ready_response`` -- answered 409 (and 422 once
   the reaper failed the stalled row). A one-off commit blip became five minutes
   of a confidently wrong terminal answer.

2. ``CACHE_ENABLED=false`` gated only the writer. The status poll still SERVED
   from Redis and still REPOPULATED it, so the operator's only lever for taking a
   misbehaving cache out of the answer path did nothing for the one endpoint that
   prefers cache over the row.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import routes
from src.config import clear_settings_cache

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient

    from src.services.deck_builder import DeckBuilder

from .test_routes_coverage import TEST_TENANT, _make_app

# `_process_deck` re-establishes identity via `set_identity_context`, which parses
# the tenant and raises on a slug -- so this must be a real uuid.
TENANT = "0195f48b-d000-7000-8000-0000000000ab"


class _RecordingRedis:
    """Captures every cache write so a test can assert on what was published."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, Any]] = []
        self.reads: list[str] = []

    async def get(self, key: str) -> Any:
        self.reads.append(key)
        return {"status": "completed", "slide_count": 99}

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        self.writes.append((key, value))


def _session_factory(*, fail_on_call: int | None = None, row: Any = None):
    """Build a fake ``get_db_session`` whose Nth (1-based) use raises on commit.

    Mirrors the real failure: the block body runs, and the error surfaces when the
    context manager exits (i.e. at COMMIT), which is exactly the transient this
    fix is about.
    """
    calls = {"n": 0}

    @contextlib.asynccontextmanager
    async def _factory():
        calls["n"] += 1
        this_call = calls["n"]

        class _Result:
            def scalar_one_or_none(self) -> Any:
                return row

        class _Session:
            async def execute(self, *_a: Any, **_k: Any) -> _Result:
                return _Result()

        yield _Session()
        if fail_on_call is not None and this_call == fail_on_call:
            raise RuntimeError("commit failed: connection reset by peer")

    return _factory, calls


async def _run_process_deck(monkeypatch, *, fail_on_call: int | None) -> _RecordingRedis:
    redis = _RecordingRedis()
    deck_row = SimpleNamespace(
        status="processing",
        manifest=None,
        pptx_storage_path=None,
        slide_count=None,
        error=None,
    )
    factory, _calls = _session_factory(fail_on_call=fail_on_call, row=deck_row)

    monkeypatch.setattr(routes, "get_db_session", factory)

    async def _acquire(_redis: Any, _job_id: str) -> bool:
        return True

    async def _release(_redis: Any, _job_id: str) -> None:
        return None

    monkeypatch.setattr(routes, "acquire_job_lock", _acquire)
    monkeypatch.setattr(routes, "release_job_lock", _release)

    class _Builder:
        async def prepare_templates(self, _slides: Any, _db: Any) -> tuple[dict, dict]:
            return {}, {}

        async def build_deck(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            # Build SUCCEEDS: artifacts are in storage. Only the completion write
            # is at issue.
            return {"slide_count": 4}

    await routes._process_deck(
        cast("DeckBuilder", _Builder()),
        "job-completion",
        {"slides": []},
        SimpleNamespace(),
        cast("RedisClient", redis),
        TENANT,
    )
    return redis


@pytest.mark.asyncio
async def test_completed_is_cached_when_the_db_write_lands(monkeypatch):
    """CONTROL: on the happy path the completion IS published to the cache.

    Without this, the guard test below would pass on code that simply never
    caches anything.
    """
    redis = await _run_process_deck(monkeypatch, fail_on_call=None)

    assert [v["status"] for _k, v in redis.writes] == ["completed"]


@pytest.mark.asyncio
async def test_completed_is_not_cached_when_the_db_write_fails(monkeypatch):
    """The bug: a failed completion commit must not publish a terminal status.

    Call 2 of ``get_db_session`` is the completion write (call 1 is
    ``prepare_templates``). When it raises, the row stays non-terminal -- so the
    cache must stay silent rather than answer ``completed`` for 300s while
    ``GET /decks/{id}/pptx`` answers 409.
    """
    redis = await _run_process_deck(monkeypatch, fail_on_call=2)

    assert redis.writes == [], (
        f"a terminal 'completed' was cached even though the DB completion write "
        f"failed: {redis.writes!r}"
    )


@pytest.mark.asyncio
async def test_disabled_cache_is_not_read_on_the_status_poll(monkeypatch):
    """``CACHE_ENABLED=false`` must take Redis out of the ANSWER path.

    The fake returns ``completed``/99 while the DB row says ``processing``/0, so a
    cache read is unmistakable in the response body.
    """
    monkeypatch.setenv("CACHE_ENABLED", "false")
    clear_settings_cache()
    try:
        redis = _RecordingRedis()
        job = SimpleNamespace(
            job_id="job-disabled",
            status="processing",
            slide_count=0,
            created_at=None,
            error=None,
        )
        app = _make_app(row=job, redis=redis)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/decks/job-disabled")

        assert response.status_code == 200
        assert response.json()["status"] == "processing", (
            "the disabled cache was still served to the caller"
        )
        assert redis.reads == [], "the disabled cache was still read"
        assert redis.writes == [], "the disabled cache was still repopulated"
    finally:
        monkeypatch.delenv("CACHE_ENABLED", raising=False)
        clear_settings_cache()


@pytest.mark.asyncio
async def test_enabled_cache_is_still_read_on_the_status_poll(monkeypatch):
    """CONTROL for the test above: with the cache ON, Redis still wins."""
    monkeypatch.setenv("CACHE_ENABLED", "true")
    clear_settings_cache()
    try:
        redis = _RecordingRedis()
        job = SimpleNamespace(
            job_id="job-enabled",
            status="processing",
            slide_count=0,
            created_at=None,
            error=None,
        )
        app = _make_app(row=job, redis=redis)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/decks/job-enabled")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert redis.reads == ["slide:job:job-enabled:status"]
    finally:
        monkeypatch.delenv("CACHE_ENABLED", raising=False)
        clear_settings_cache()


# ``TEST_TENANT`` is imported for the shared app factory's tenant header; re-export
# it so a linter does not read the import as unused.
__all__ = ["TEST_TENANT"]
