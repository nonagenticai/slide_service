"""Tests for src.asgi startup hook and route wiring.

Covers Redis-fail and seed-fail fallbacks (startup must continue) and asserts
the root / health / deck-status routes are actually SERVED by the
create_app-built app (requested through TestClient, never inspected as route
shape -- see ``test_root_is_routed``). Since adopting ``w2v_common.asgi.create_app`` the monolithic ``lifespan``
became ``on_startup`` / ``on_shutdown`` hooks; DB/Redis symbols are imported
inside the hooks, so tests patch them at their source modules.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _fake_app() -> FastAPI:
    # A real FastAPI instance so `.state` is a genuine Starlette State the
    # startup hook can populate (and so ty accepts it as the hook argument).
    return FastAPI()


@pytest.mark.asyncio
async def test_startup_redis_fail_continues():
    """on_startup must complete even when Redis init raises."""
    from src import asgi

    storage_mock = MagicMock()
    storage_mock.initialize = AsyncMock()

    app = _fake_app()
    with (
        patch(
            "src.database.connection.get_engine", new_callable=AsyncMock, return_value=AsyncMock()
        ),
        patch(
            "src.cache.redis_client.init_redis",
            new_callable=AsyncMock,
            side_effect=ConnectionError("nope"),
        ),
        patch("src.cache.redis_client.get_redis_client", return_value=None),
        patch("src.seed.seed_templates.seed_templates", new_callable=AsyncMock, return_value=0),
        patch("src.services.storage_service.StorageService", return_value=storage_mock),
        patch("src.services.deck_builder.DeckBuilder", return_value=MagicMock()),
        patch("src.asgi._start_browser_pool", new_callable=AsyncMock),
    ):
        await asgi.on_startup(app)
        # Startup continued past the Redis failure and built the core services.
        assert hasattr(app.state, "deck_builder")


@pytest.mark.asyncio
async def test_startup_seed_fail_does_not_raise():
    """on_startup logs but does not raise when template seeding fails."""
    from src import asgi

    storage_mock = MagicMock()
    storage_mock.initialize = AsyncMock()

    redis_mock = AsyncMock()
    redis_mock.ping = AsyncMock(return_value=True)

    app = _fake_app()
    with (
        patch(
            "src.database.connection.get_engine", new_callable=AsyncMock, return_value=AsyncMock()
        ),
        patch("src.cache.redis_client.init_redis", new_callable=AsyncMock, return_value=redis_mock),
        patch("src.cache.redis_client.get_redis_client", return_value=redis_mock),
        patch(
            "src.seed.seed_templates.seed_templates",
            new_callable=AsyncMock,
            side_effect=RuntimeError("seed boom"),
        ),
        patch("src.services.storage_service.StorageService", return_value=storage_mock),
        patch("src.services.deck_builder.DeckBuilder", return_value=MagicMock()),
        patch("src.asgi._start_browser_pool", new_callable=AsyncMock),
    ):
        await asgi.on_startup(app)
        assert hasattr(app.state, "storage_service")


@pytest.mark.asyncio
async def test_startup_seed_fail_is_recorded_not_swallowed():
    """A failed seed must leave a NOT-ok status on app.state naming the consequence.

    Regression guard for 2026-08-19: ``on_startup`` caught the RLS
    ``InsufficientPrivilege`` from the seed, logged one line and let both prod
    replicas go Ready with 5 of 16 system templates missing. The process is still
    allowed to start (a re-raise crashloops every replica) but the outcome must
    now be reachable by the health surface.
    """
    from src import asgi

    with (
        patch(
            "src.seed.seed_templates.seed_templates",
            new_callable=AsyncMock,
            side_effect=RuntimeError("new row violates row-level security policy"),
        ),
    ):
        status = await asgi._seed_system_templates()

    assert status.ok is False
    assert "row-level security" in status.detail
    assert "degrade" in status.detail


@pytest.mark.asyncio
async def test_startup_reports_missing_templates_even_when_seed_reports_success():
    """Seed returning 0 is NOT proof the templates are there -- verify and report."""
    from src import asgi

    with (
        patch("src.seed.seed_templates.seed_templates", new_callable=AsyncMock, return_value=0),
        patch(
            "src.seed.seed_templates.missing_system_templates",
            new_callable=AsyncMock,
            return_value={"quote", "stat"},
        ),
    ):
        status = await asgi._seed_system_templates()

    assert status.ok is False
    assert "quote" in status.detail and "stat" in status.detail


@pytest.mark.asyncio
async def test_startup_reports_healthy_when_all_templates_present():
    """The happy path must be distinguishable from the fault path."""
    from src import asgi

    with (
        patch("src.seed.seed_templates.seed_templates", new_callable=AsyncMock, return_value=16),
        patch(
            "src.seed.seed_templates.missing_system_templates",
            new_callable=AsyncMock,
            return_value=set(),
        ),
    ):
        status = await asgi._seed_system_templates()

    assert status.ok is True


def _client() -> TestClient:
    """A TestClient that does NOT run the lifespan.

    Constructing without the ``with`` block deliberately skips on_startup, so
    these tests need no DB/Redis/browser-pool. Routing is resolved by the
    router regardless, which is the only thing under test here.
    """
    from src.asgi import app

    return TestClient(app, raise_server_exceptions=False)


def test_root_is_routed():
    """The app must ROUTE ``GET /`` -- asserted by request, not by shape.

    The predecessors of this test asserted ``"/" in {route.path ...}``. That is
    a statement about FastAPI's internal route-tree layout, not about the app,
    and it broke on the starlette 1.3->1.6 / fastapi 0.135->0.141 bump purely
    because included-router routes are no longer flattened onto ``app.routes``
    -- while every endpoint still served correctly. Only a 404 proves the app
    does not route; 401/403/422/500 all prove that it does.
    """
    response = _client().get("/")
    assert response.status_code != 404, response.text


def test_health_is_routed():
    """``GET /health`` must be routed (same behavioural contract as above)."""
    response = _client().get("/health")
    assert response.status_code != 404, response.text


def test_deck_status_poll_is_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /api/decks/{job_id}`` must be routed -- the status-poll surface.

    Regression guard: when this path stops being served the frontend polls a
    404 forever and a finished deck never surfaces.

    The auth stub is load-bearing, NOT boilerplate. Without a license token the
    middleware short-circuits ``/api/*`` with 401 *before* the router is
    consulted, so an unauthenticated ``!= 404`` assertion passes identically
    whether the decks router is registered or not -- i.e. it would be vacuous.
    Measured with the router removed: unauthenticated 401 (indistinguishable),
    authenticated 404 ``HTTP_404``. With the router registered the request
    reaches the handler (500 -- no DB session under a lifespan-less client),
    which is exactly the point: anything but 404 proves the app routed it.
    """
    import w2v_common.auth.middleware as w2v_auth

    async def _fake_introspect(_token: str) -> dict[str, object]:
        return {
            "active": True,
            "tenant_id": "0195f48b-d000-7000-8000-000000000001",
            "subject_id": "subj-1",
            "license_id": "lic-1",
            "roles": ["user"],
        }

    monkeypatch.setattr(w2v_auth, "_introspect_license", _fake_introspect)

    response = _client().get(
        "/api/decks/00000000-0000-0000-0000-000000000000",
        headers={"X-Tenant-Id": "Bearer abc.def.ghi"},
    )
    # A ROUTED request reaches the handler: it succeeds, raises, or 404s with the
    # HANDLER's own message. Only the ROUTER's generic 404 -- raised before any
    # handler runs -- disproves routing.
    #
    # ⚠️ `!= 404` ALONE WAS WRONG, and it went red the first time the suite
    # actually executed. `GET /api/decks/<unknown-uuid>` legitimately 404s FROM
    # THE HANDLER ("Job not found") once a DB session is reachable; that is proof
    # of routing, not absence of it. The original guard only held while the
    # handler happened to crash with a 500 under a lifespan-less client -- i.e.
    # it was passing for an incidental reason, not the one it claimed.
    assert response.status_code != 404 or "Job not found" in response.text, response.text


# ── The startup "Database connected" line must never carry the DSN password ──
#
# REGRESSION: `on_startup` logged `settings.database.url` raw — the full
# postgresql+psycopg://<user>:<password>@host:port/db DSN — at INFO on the
# connect path, so the live app password landed in this pod's stdout and in
# anything shipping those logs, indefinitely.
#
# The test drives the REAL `on_startup` rather than the redactor, because the
# defect was at the CALL SITE: a test that only exercises
# `w2v_common.observability.database_endpoint` passes for the whole duration of
# the leak. That helper has its own unit tests in w2v_common; this one exists to
# fail if anyone hands the raw url back to the logger here.

# Wholly synthetic and shares no prefix with any real credential — this string is
# committed, printed by pytest on failure, and ends up in CI logs, i.e. exactly
# the places this change exists to keep secrets out of.
_FAKE_PASSWORD = "not-a-real-password-xyzzy"  # pragma: allowlist secret
_FAKE_DSN = (
    f"postgresql+psycopg://slide_app:{_FAKE_PASSWORD}@pgbouncer.pgbouncer:6432/slide_service"
)


@pytest.mark.asyncio
async def test_startup_db_log_does_not_leak_the_dsn_password(monkeypatch: pytest.MonkeyPatch):
    """The emitted startup log record must not contain the DSN password."""
    import io
    import logging

    from src import asgi

    monkeypatch.setattr(asgi.settings.database, "url", _FAKE_DSN)

    # A handler attached directly to the module logger, NOT ``caplog``. This
    # service calls ``configure_logging()``, which re-points the root handlers,
    # so caplog's root handler observes nothing and every assertion below would
    # pass vacuously. Learned the hard way: the first draft of this test went
    # green while the line was emitting somewhere caplog could not see.
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.INFO)
    asgi.logger.addHandler(handler)
    # ``setLevel`` and NOT ``logger.level = ...``: ``logging.Logger`` memoizes
    # ``isEnabledFor`` results in ``Logger._cache``, and only ``setLevel()``
    # invalidates that cache. Assigning ``.level`` directly leaves a stale entry
    # behind, so a logger already probed while it was at WARNING would keep
    # suppressing the record this test needs to see.
    previous_level = asgi.logger.level
    asgi.logger.setLevel(logging.INFO)

    storage_mock = MagicMock()
    storage_mock.initialize = AsyncMock()

    app = _fake_app()
    try:
        with (
            patch(
                "src.database.connection.get_engine",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            # Seeding opens its OWN connection and burns ~60s on the retry
            # ladder before failing soft; it is irrelevant to this assertion.
            patch("src.asgi._seed_system_templates", new_callable=AsyncMock, return_value={}),
            patch("src.cache.redis_client.init_redis", new_callable=AsyncMock),
            patch("src.cache.redis_client.get_redis_client", return_value=None),
            patch("src.services.storage_service.StorageService", return_value=storage_mock),
            patch("src.services.deck_builder.DeckBuilder", return_value=MagicMock()),
            patch("src.asgi._start_browser_pool", new_callable=AsyncMock),
        ):
            await asgi.on_startup(app)
    finally:
        asgi.logger.removeHandler(handler)
        asgi.logger.setLevel(previous_level)

    captured = buffer.getvalue()
    # Guard against a vacuous pass: if the line never emitted, the assertions
    # below would hold for the wrong reason.
    assert "Database connected" in captured, (
        "the 'Database connected' line never emitted; the assertions would be vacuous"
    )
    assert _FAKE_PASSWORD not in captured, "DSN password leaked into the startup log"
    assert "slide_app" not in captured, "DSN username leaked into the startup log"
    # Still useful, or the next person deletes the log line instead of keeping it.
    assert "pgbouncer.pgbouncer:6432/slide_service" in captured


# ─── The startup "Redis connected" line must never carry the Redis password ───
#
# REGRESSION (the SIBLING of the DSN leak above): `on_startup` logged
# `settings.redis.url` raw — the full `redis://[<user>]:<password>@host:port/db`
# DSN — at INFO on the connect path, fifty lines below the "Database connected"
# line that was already redacted. Guarded-beside-unguarded: fixing only the
# first one left the live Redis password landing in this pod's stdout, and in
# anything shipping those logs, on every single pod start.
#
# The test drives the REAL `on_startup` rather than the redactor, for the same
# reason the DSN test above does: the defect is at the CALL SITE, so a test that
# only exercises `w2v_common.observability.redis_endpoint` stays green for the
# entire duration of the leak. That helper has its own unit tests in w2v_common;
# this one exists to fail if anyone hands the raw url back to the logger here.

_FAKE_REDIS_URL = f"redis://slides:{_FAKE_PASSWORD}@redis.redis:6379/3"


@pytest.mark.asyncio
async def test_startup_redis_log_does_not_leak_the_redis_password(
    monkeypatch: pytest.MonkeyPatch,
):
    """The emitted startup log record must not contain the Redis password."""
    import io
    import logging

    from src import asgi

    monkeypatch.setattr(asgi.settings.redis, "url", _FAKE_REDIS_URL)

    # A handler on the module logger, NOT ``caplog`` -- ``configure_logging()``
    # re-points the root handlers, so caplog observes nothing here and every
    # assertion below would pass vacuously. And ``setLevel`` and NOT
    # ``logger.level = ...``: ``logging.Logger`` memoizes ``isEnabledFor`` in
    # ``Logger._cache`` and only ``setLevel()`` invalidates it, so assigning
    # ``.level`` directly leaves a stale entry that keeps suppressing the record
    # this test needs to see.
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.INFO)
    asgi.logger.addHandler(handler)
    previous_level = asgi.logger.level
    asgi.logger.setLevel(logging.INFO)

    storage_mock = MagicMock()
    storage_mock.initialize = AsyncMock()

    app = _fake_app()
    try:
        with (
            patch(
                "src.database.connection.get_engine",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            # Seeding opens its OWN connection and burns ~60s on the retry
            # ladder before failing soft; irrelevant to this assertion.
            patch("src.asgi._seed_system_templates", new_callable=AsyncMock, return_value={}),
            # ``init_redis`` must SUCCEED here -- the log line under test sits
            # after ``await redis_client.ping()`` inside the try, so a failing
            # Redis would skip it and the positive control below would fire.
            patch("src.cache.redis_client.init_redis", new_callable=AsyncMock),
            patch("src.cache.redis_client.get_redis_client", return_value=None),
            patch("src.services.storage_service.StorageService", return_value=storage_mock),
            patch("src.services.deck_builder.DeckBuilder", return_value=MagicMock()),
            patch("src.asgi._start_browser_pool", new_callable=AsyncMock),
        ):
            await asgi.on_startup(app)
    finally:
        asgi.logger.removeHandler(handler)
        asgi.logger.setLevel(previous_level)

    captured = buffer.getvalue()
    # Guard against a vacuous pass: if the line never emitted, the assertions
    # below would hold for the wrong reason.
    assert "Redis connected" in captured, (
        "the 'Redis connected' line never emitted; the assertions would be vacuous"
    )
    assert _FAKE_PASSWORD not in captured, "Redis password leaked into the startup log"
    assert "slides:" not in captured, "Redis username leaked into the startup log"
    # Still useful, or the next person deletes the log line instead of keeping
    # it. ``redis_endpoint`` renders host:port only -- no scheme, no db index.
    assert "redis.redis:6379" in captured
