"""Tests for template CRUD and deck status API routes."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.routes import router
from src.config import clear_settings_cache
from src.database.session import get_db

#: These tests stub the DB session, so the tenant filter is not what they exercise
#: -- but ``_get_tenant_id`` now raises 401 when no identity is on the request
#: (the ``"default"`` sentinel is retired), so every request needs a real tenant
#: uuid. ``tests/unit/test_routes_tenant_isolation`` owns the proof that the
#: filter itself works, and ``test_missing_tenant_returns_401`` there owns the
#: proof that an identity-less request is rejected.
TEST_TENANT = "0195f48b-d000-7000-8000-0000000000aa"


class _ExecuteResult:
    def __init__(self, row: object | None = None, rows: list | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self) -> object | None:
        return self._row

    def scalar(self) -> int | None:
        return len(self._rows)

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._rows)


class _ScalarsResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _StubDBSession:
    def __init__(
        self,
        row: object | None = None,
        rows: list | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or []
        self._added: list = []

    async def execute(self, _stmt: object) -> _ExecuteResult:
        return _ExecuteResult(self._row, self._rows)

    def add(self, obj: object) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


def _make_test_app(
    row: object | None = None,
    rows: list | None = None,
    *,
    storage: object | None = None,
    redis: object | None = None,
) -> FastAPI:
    app = FastAPI(title="slide-service-test")

    async def override_db() -> AsyncGenerator[_StubDBSession]:
        yield _StubDBSession(row, rows)

    app.dependency_overrides[get_db] = override_db
    if storage is not None:
        app.state.storage_service = storage
    if redis is not None:
        app.state.redis_client = redis

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant_id = TEST_TENANT
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None]:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.asyncio
async def test_list_templates_empty():
    """Empty template list returns paginated response."""
    app = _make_test_app(rows=[])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/templates")
    assert response.status_code == 200
    data = response.json()
    assert data["templates"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_template_not_found():
    """Get nonexistent template returns 404."""
    app = _make_test_app(row=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/templates/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_template_found():
    """Get existing template returns template data."""
    template = SimpleNamespace(
        template_id="t-1",
        name="title",
        display_name="Title Slide",
        category="narrative",
        layout_type="title",
        slot_definitions={"title": {"type": "text"}},
        styling_rules={"layout": "centered"},
        default_transition=None,
        default_timing_hint_seconds=8.0,
        is_system=True,
        is_active=True,
    )
    app = _make_test_app(row=template)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/templates/t-1")
    assert response.status_code == 200
    assert response.json()["name"] == "title"


@pytest.mark.asyncio
async def test_delete_template_not_found():
    """Delete nonexistent template returns 404."""
    app = _make_test_app(row=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/templates/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_system_template_forbidden():
    """Cannot delete system templates."""
    template = SimpleNamespace(
        template_id="t-1",
        is_system=True,
        is_active=True,
    )
    app = _make_test_app(row=template)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/templates/t-1")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_deck_status_not_found():
    """Poll nonexistent job returns 404."""
    app = _make_test_app(row=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_deck_status_found():
    """Poll existing job returns status."""
    job = SimpleNamespace(
        job_id="j-1",
        status="completed",
        slide_count=5,
        created_at=None,
        error=None,
    )
    app = _make_test_app(row=job)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/j-1")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["slide_count"] == 5


@pytest.mark.asyncio
async def test_get_manifest_not_completed():
    """Manifest download for non-completed job returns 409."""
    job = SimpleNamespace(
        job_id="j-1",
        status="processing",
        manifest=None,
    )
    app = _make_test_app(row=job)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/j-1/manifest")
    assert response.status_code == 409
    assert response.headers.get("retry-after") == "5"
    data = response.json()
    assert data["error"]["message"] == "Deck is still being generated"
    assert data["error"]["code"] == "DECK_NOT_READY"
    assert data["error"]["status"] == 409
    assert data["error"]["job_status"] == "processing"


@pytest.mark.asyncio
async def test_download_pptx_not_completed():
    """PPTX download for non-completed job returns 409."""
    job = SimpleNamespace(
        job_id="j-1",
        status="processing",
        manifest=None,
        pptx_storage_path=None,
    )
    app = _make_test_app(row=job)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/j-1/pptx")
    assert response.status_code == 409
    assert response.headers.get("retry-after") == "5"
    data = response.json()
    assert data["error"]["message"] == "Deck is still being generated"
    assert data["error"]["code"] == "DECK_NOT_READY"
    assert data["error"]["status"] == 409
    assert data["error"]["job_status"] == "processing"


@pytest.mark.asyncio
async def test_get_manifest_completed():
    """Manifest download for completed job returns manifest JSON."""
    job = SimpleNamespace(
        job_id="j-1",
        status="completed",
        manifest={
            "manifest_version": "1.0",
            "deck_id": "j-1",
            "title": "Test",
            "aspect_ratio": "16:9",
            "slide_count": 1,
            "total_estimated_duration_seconds": 10.0,
            "brand": {},
            "assets": {},
            "slides": [],
        },
    )
    app = _make_test_app(row=job)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/j-1/manifest")
    assert response.status_code == 200
    data = response.json()
    assert data["deck_id"] == "j-1"


@pytest.mark.asyncio
async def test_update_template_not_found():
    """Updating a nonexistent template returns 404."""
    app = _make_test_app(row=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/templates/nonexistent",
            json={"display_name": "Updated"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_system_template_forbidden():
    """Updating a system template returns 403."""
    template = SimpleNamespace(
        template_id="t-1",
        name="title",
        display_name="Title",
        category="narrative",
        layout_type="title",
        slot_definitions={},
        styling_rules={},
        default_transition=None,
        default_timing_hint_seconds=10.0,
        is_system=True,
        is_active=True,
    )
    app = _make_test_app(row=template)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/templates/t-1",
            json={"display_name": "Updated"},
        )
    assert response.status_code == 403
