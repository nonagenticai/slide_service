"""Tests for health check endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from src.api.health import router
from src.services.browser_pool import PoolStatus

_HEALTHY_POOL = PoolStatus(started=True, capacity=2, idle=2, live=1)
_DEAD_POOL = PoolStatus(started=False, capacity=0, idle=0, live=0)


class _FakeTask:
    """Stands in for the reaper asyncio.Task: only ``done()`` is on the health path."""

    def __init__(self, *, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


@pytest.fixture(autouse=True)
def _healthy_browser_pool():
    """Default the Chromium pool to started, so each test names its own fault."""
    with patch("src.api.health.pool_status", return_value=_HEALTHY_POOL):
        yield


def _make_health_app(*, reaper_done: bool = False, attach_reaper: bool = True) -> FastAPI:
    """Build the health app. Defaults to a running reaper loop."""
    app = FastAPI(title="health-test")
    app.include_router(router)
    if attach_reaper:
        app.state.reaper_task = _FakeTask(done=reaper_done)
    return app


@pytest.mark.asyncio
async def test_health_live():
    """Liveness probe always returns ok."""
    app = _make_health_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_success():
    """Readiness probe returns ready when DB is up."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_engine = AsyncMock()
    # Make connect() return an async context manager yielding mock_conn
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_engine.connect = MagicMock(return_value=cm)

    with patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=mock_engine):
        app = _make_health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_ready_db_down():
    """Readiness probe returns 503 when DB is down."""
    with patch(
        "src.api.health.get_engine",
        new_callable=AsyncMock,
        side_effect=ConnectionError("no db"),
    ):
        app = _make_health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["ready"] is False
    assert data["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_full_health_check_healthy():
    """Full health endpoint returns healthy when DB and Redis are up."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_engine = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_engine.connect = MagicMock(return_value=cm)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)

    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=mock_engine),
        patch("src.api.health.get_redis_client", return_value=mock_redis),
    ):
        app = _make_health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["components"]["database"] == "healthy"
    assert data["components"]["redis"] == "healthy"
    assert data["components"]["api"] == "healthy"


@pytest.mark.asyncio
async def test_full_health_check_no_redis():
    """Full health returns not configured when Redis client is None."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_engine = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_engine.connect = MagicMock(return_value=cm)

    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=mock_engine),
        patch("src.api.health.get_redis_client", return_value=None),
    ):
        app = _make_health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["components"]["redis"] == "not configured"


@pytest.mark.asyncio
async def test_full_health_check_db_down():
    """Full health returns degraded when DB is unhealthy."""
    with (
        patch(
            "src.api.health.get_engine",
            new_callable=AsyncMock,
            side_effect=ConnectionError("db down"),
        ),
        patch("src.api.health.get_redis_client", return_value=None),
    ):
        app = _make_health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert "unhealthy" in data["components"]["database"]


@pytest.mark.asyncio
async def test_full_health_check_redis_ping_fails():
    """Full health returns degraded when Redis ping fails."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_engine = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_engine.connect = MagicMock(return_value=cm)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=False)

    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=mock_engine),
        patch("src.api.health.get_redis_client", return_value=mock_redis),
    ):
        app = _make_health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["components"]["redis"] == "unhealthy: ping failed"


# ---------------------------------------------------------------------------
# The components that do the WORK: the Chromium pool that renders slides and
# the reaper loop that unwedges dead deck jobs. Each state below is one in which
# the old health check said "healthy" while the pod rendered nothing.
# ---------------------------------------------------------------------------


def _up_engine():
    """An engine whose connect() context manager yields a working connection."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = AsyncMock()
    engine.connect = MagicMock(return_value=cm)
    return engine


async def _get_health(app: FastAPI) -> dict:
    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=_up_engine()),
        patch("src.api.health.get_redis_client", return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_health_unhealthy_when_browser_pool_never_started():
    """The fail-soft startup launch failed: no Chromium, every render 500s."""
    app = _make_health_app()
    with patch("src.api.health.pool_status", return_value=_DEAD_POOL):
        data = await _get_health(app)

    assert data["status"] == "unhealthy"
    assert "cannot render a slide" in data["components"]["browser_pool"]
    # The dependencies are fine; the fault must be attributed to the renderer.
    assert data["components"]["database"] == "healthy"


@pytest.mark.asyncio
async def test_health_unhealthy_when_browser_pool_has_no_capacity():
    """Started but zero slots -- 'the object exists' is not a capacity signal."""
    app = _make_health_app()
    with patch(
        "src.api.health.pool_status",
        return_value=PoolStatus(started=True, capacity=0, idle=0, live=0),
    ):
        data = await _get_health(app)

    assert data["status"] == "unhealthy"
    assert data["components"]["browser_pool"].startswith("unhealthy")


@pytest.mark.asyncio
async def test_health_degraded_when_the_reaper_loop_has_exited():
    """A dead reaper leaves decks wedged at 'processing' and nothing ever complains."""
    app = _make_health_app(reaper_done=True)
    data = await _get_health(app)

    assert data["status"] == "degraded"
    assert "has exited" in data["components"]["deck_reaper"]


@pytest.mark.asyncio
async def test_health_degraded_when_the_reaper_was_enabled_but_never_started():
    """Wiring raised inside the fail-soft startup block -- app.state.reaper_task is None."""
    app = _make_health_app(attach_reaper=False)
    data = await _get_health(app)

    assert data["status"] == "degraded"
    assert "never started" in data["components"]["deck_reaper"]


@pytest.mark.asyncio
async def test_health_reports_a_live_pool_and_reaper_as_healthy():
    app = _make_health_app()
    data = await _get_health(app)

    assert data["components"]["browser_pool"].startswith("healthy")
    assert data["components"]["deck_reaper"] == "healthy"


@pytest.mark.asyncio
async def test_readiness_stays_green_when_the_browser_pool_is_dead():
    """Deliberate: the pool is start-on-demand, so eviction is self-defeating.

    Failing readiness would withhold the very request that relaunches the pool,
    turning a transient startup failure into a permanent one.
    """
    app = _make_health_app()
    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=_up_engine()),
        patch("src.api.health.pool_status", return_value=_DEAD_POOL),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_browser_pool_status_does_not_start_the_pool(monkeypatch: pytest.MonkeyPatch):
    """The probe must report the pool the render path sees, not create one."""
    import src.services.browser_pool as bp

    monkeypatch.setattr(bp, "_pool", None)
    assert bp.pool_status() == _DEAD_POOL
    assert bp._pool is None, "pool_status() must never construct the pool"


# ---------------------------------------------------------------------------
# A real dead Postgres does not raise OSError. It raises
# ``sqlalchemy.exc.OperationalError``, which inherits ``SQLAlchemyError`` and
# neither ``OSError`` nor ``RuntimeError`` -- so the narrow catch these handlers
# used to have let the outage escape and K8s read a 500 ("app broken") where it
# needed a 503 ("not ready"). These tests pin the driver exception itself, not a
# stand-in that happens to be an OSError.
# ---------------------------------------------------------------------------


def _operational_error() -> OperationalError:
    """The exception a live pod gets when Postgres is gone."""
    return OperationalError(
        "SELECT 1",
        None,
        Exception("connection to server at postgres.test failed: Connection refused"),
    )


def _down_engine() -> AsyncMock:
    """An engine that connects, then fails the probe query like a dead DB does."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=_operational_error())
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = AsyncMock()
    engine.connect = MagicMock(return_value=cm)
    return engine


@pytest.mark.asyncio
async def test_health_ready_returns_503_not_500_on_sqlalchemy_operational_error() -> None:
    """Readiness must answer 503 when the probe query raises OperationalError."""
    app = _make_health_app()
    with patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=_down_engine()):
        # raise_app_exceptions=False so an UNCAUGHT handler exception becomes the
        # 500 a real server would return, instead of blowing up the test as an
        # error. That keeps the failure mode legible: 503 vs 500, which is
        # exactly the distinction this fix is about.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/ready")

    assert response.status_code == 503, (
        f"dead-DB readiness must be 503, got {response.status_code} "
        "-- the driver exception escaped the handler"
    )
    data = response.json()
    assert data["ready"] is False
    assert data["checks"]["database"]["status"] == "down"


@pytest.mark.asyncio
async def test_full_health_reports_degraded_on_sqlalchemy_operational_error() -> None:
    """/health must attribute the fault to the DB component, not 500 outright."""
    app = _make_health_app()
    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=_down_engine()),
        patch("src.api.health.get_redis_client", return_value=None),
    ):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200, f"expected a reported degrade, got {response.status_code}"
    data = response.json()
    assert data["status"] == "degraded"
    assert data["components"]["database"].startswith("unhealthy")


# ---------------------------------------------------------------------------
# System templates -- the 2026-08-19 silent-degradation guard
# ---------------------------------------------------------------------------


def _mock_engine() -> MagicMock:
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    engine = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=cm)
    return engine


async def _health_json(app: FastAPI) -> dict:
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    with (
        patch("src.api.health.get_engine", new_callable=AsyncMock, return_value=_mock_engine()),
        patch("src.api.health.get_redis_client", return_value=mock_redis),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return (await client.get("/health")).json()


@pytest.mark.asyncio
async def test_health_unhealthy_when_system_templates_missing():
    """A failed/incomplete template seed must make /health say so, loudly.

    On 2026-08-19 both prod replicas served for a day with 5 of 16 system
    templates missing because the seed error was swallowed at startup and NO
    surface reported it: /health said healthy. This is that surface.
    """
    from src.seed.seed_templates import SystemTemplateStatus

    app = _make_health_app()
    app.state.system_templates = SystemTemplateStatus(
        ok=False, detail="unhealthy: 5 of 16 system templates MISSING (quote, stat) -- degrade"
    )

    data = await _health_json(app)

    assert data["status"] == "unhealthy", data
    assert "MISSING" in data["components"]["system_templates"]
    assert "quote" in data["components"]["system_templates"]


@pytest.mark.asyncio
async def test_health_healthy_when_system_templates_present():
    """The component must not manufacture a fault on the happy path."""
    from src.seed.seed_templates import SystemTemplateStatus

    app = _make_health_app()
    app.state.system_templates = SystemTemplateStatus(ok=True, detail="healthy: all 16 present")

    data = await _health_json(app)

    assert data["status"] == "healthy", data
    assert data["components"]["system_templates"] == "healthy: all 16 present"
