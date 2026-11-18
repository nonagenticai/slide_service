"""Route coverage tests for deck creation, status, downloads and list_templates filters."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.routes import _get_storage, router
from src.config import clear_settings_cache
from src.database.session import get_db

#: These tests stub the DB session, so the tenant filter is not what they exercise
#: -- but ``_get_tenant_id`` now raises 401 when no identity is on the request
#: (the ``"default"`` sentinel is retired), so every request needs a real tenant
#: uuid. ``tests/unit/test_routes_tenant_isolation`` owns the proof that the
#: filter itself works, and ``test_missing_tenant_returns_401`` there owns the
#: proof that an identity-less request is rejected.
TEST_TENANT = "0195f48b-d000-7000-8000-0000000000aa"


# ---------------------------------------------------------------------------
# DB / Storage stubs
# ---------------------------------------------------------------------------


class _ExecuteResult:
    def __init__(self, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._row

    def scalar(self):
        return len(self._rows)

    def scalars(self):
        return _Scalars(self._rows)


class _Scalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _StubDB:
    def __init__(self, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows or []
        self.added: list = []

    async def execute(self, _stmt):
        return _ExecuteResult(self._row, self._rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


class _StubStorage:
    def __init__(self, *, backend_type: str = "local", files: dict | None = None) -> None:
        self.backend_type = backend_type
        self._files = files or {}

    async def exists(self, path: str) -> bool:
        return path in self._files

    async def read_file(self, path: str) -> bytes:
        return self._files[path]

    def get_full_path(self, path: str) -> str:
        raise NotImplementedError


def _make_app(
    row=None,
    rows=None,
    *,
    storage=None,
    redis=None,
    deck_builder=None,
) -> FastAPI:
    app = FastAPI(title="test")

    async def override_db():
        yield _StubDB(row, rows)

    app.dependency_overrides[get_db] = override_db
    if storage is not None:
        app.state.storage_service = storage
    if redis is not None:
        app.state.redis_client = redis
    if deck_builder is not None:
        app.state.deck_builder = deck_builder

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant_id = TEST_TENANT
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
# list_templates with category/layout_type filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_with_category_filter():
    """Filtering by category passes correctly."""
    template = SimpleNamespace(
        template_id="t-1",
        name="title",
        display_name="Title Slide",
        category="narrative",
        layout_type="title",
        slot_definitions={},
        styling_rules={},
        default_transition=None,
        default_timing_hint_seconds=8.0,
        is_system=True,
        is_active=True,
    )
    app = _make_app(rows=[template])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/templates?category=narrative")
    assert response.status_code == 200
    data = response.json()
    assert len(data["templates"]) == 1


@pytest.mark.asyncio
async def test_list_templates_with_layout_type_filter():
    """Filtering by layout_type passes correctly."""
    template = SimpleNamespace(
        template_id="t-2",
        name="scorecard",
        display_name="Scorecard",
        category="analysis",
        layout_type="scorecard",
        slot_definitions={},
        styling_rules={},
        default_transition=None,
        default_timing_hint_seconds=12.0,
        is_system=True,
        is_active=True,
    )
    app = _make_app(rows=[template])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/templates?layout_type=scorecard")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# create_template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_success():
    """POST /api/templates creates a new template."""
    app = _make_app(row=None, rows=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/templates",
            json={
                "name": "my_template",
                "display_name": "My Template",
                "category": "custom",
                "layout_type": "title",
                "slot_definitions": {"title": {"type": "text"}},
                "styling_rules": {},
                "default_transition": None,
                "default_timing_hint_seconds": 10.0,
            },
        )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "my_template"
    assert data["is_system"] is False


# ---------------------------------------------------------------------------
# update_template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_template_success():
    """PUT /api/templates/:id updates mutable fields."""
    template = SimpleNamespace(
        template_id="t-1",
        name="title",
        display_name="Old Name",
        category="narrative",
        layout_type="title",
        slot_definitions={},
        styling_rules={},
        default_transition=None,
        default_timing_hint_seconds=10.0,
        is_system=False,
        is_active=True,
    )
    app = _make_app(row=template)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/templates/t-1",
            json={"display_name": "New Name"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "New Name"


@pytest.mark.asyncio
async def test_update_template_all_fields():
    """PUT /api/templates/:id updates all optional fields."""
    template = SimpleNamespace(
        template_id="t-1",
        name="title",
        display_name="Old",
        category="old_cat",
        layout_type="title",
        slot_definitions={},
        styling_rules={},
        default_transition=None,
        default_timing_hint_seconds=10.0,
        is_system=False,
        is_active=True,
    )
    app = _make_app(row=template)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/templates/t-1",
            json={
                "display_name": "New",
                "category": "new_cat",
                "slot_definitions": {"title": {"type": "text"}},
                "styling_rules": {"layout": "centered"},
                "default_transition": {"type": "slide"},
                "default_timing_hint_seconds": 15.0,
            },
        )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# delete_template success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_template_success():
    """DELETE /api/templates/:id soft-deletes the template."""
    template = SimpleNamespace(
        template_id="t-1",
        is_system=False,
        is_active=True,
    )
    app = _make_app(row=template)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/templates/t-1")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["id"] == "t-1"


# ---------------------------------------------------------------------------
# get_deck_status with Redis cache hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deck_status_redis_cache_hit():
    """Status returned from Redis cache when DB confirms tenant ownership.

    The DB lookup must succeed first (tenant scoping); only then is the
    cache trusted. This prevents cross-tenant disclosure via the
    tenant-agnostic Redis key.
    """

    class _FakeRedis:
        async def get(self, key: str):
            return {"status": "completed", "slide_count": 3}

        async def set(self, key, value, *, ttl=None):
            pass

    job = SimpleNamespace(
        job_id="job-cached",
        status="processing",  # stale DB value -- cache wins
        slide_count=0,
        created_at=None,
        error=None,
    )
    app = _make_app(row=job, redis=_FakeRedis())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-cached")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["slide_count"] == 3


@pytest.mark.asyncio
async def test_get_deck_status_redis_writeback():
    """Status from DB is written back to Redis."""
    written: list = []

    class _FakeRedis:
        async def get(self, key: str):
            return None  # miss

        async def set(self, key, value, *, ttl=None):
            written.append((key, value))

    job = SimpleNamespace(
        job_id="j-2",
        status="completed",
        slide_count=2,
        created_at=None,
        error=None,
    )
    app = _make_app(row=job, redis=_FakeRedis())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/j-2")
    assert response.status_code == 200
    assert len(written) == 1


@pytest.mark.asyncio
async def test_get_deck_status_redis_read_exception():
    """Redis read exception is swallowed (fail-open); DB is still queried."""

    class _BadRedis:
        async def get(self, key: str):
            raise RuntimeError("redis down")

        async def set(self, key, value, *, ttl=None):
            pass

    job = SimpleNamespace(
        job_id="j-3",
        status="processing",
        slide_count=None,
        created_at=None,
        error=None,
    )
    app = _make_app(row=job, redis=_BadRedis())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/j-3")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"


# ---------------------------------------------------------------------------
# download_pptx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_pptx_success(tmp_path: Path):
    """PPTX download returns the file bytes."""
    pptx_path = tmp_path / "decks" / "job-1" / "deck.pptx"
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    pptx_path.write_bytes(b"PK\x03\x04fake")

    job = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={"assets": {"pptx": {"storage_path": "decks/job-1/deck.pptx"}}},
        pptx_storage_path="decks/job-1/deck.pptx",
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/pptx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )


@pytest.mark.asyncio
async def test_download_pptx_not_found():
    """PPTX download returns 404 when job does not exist."""
    app = _make_app(row=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/no-such-job/pptx")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_pptx_file_missing_from_storage(tmp_path: Path):
    """PPTX download returns 404 when file not in storage."""
    job = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={},
        pptx_storage_path="decks/job-1/deck.pptx",
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/pptx")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# download_pdf 404 paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_pdf_not_found():
    """PDF download returns 404 when job does not exist."""
    app = _make_app(row=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/no-such-job/pdf")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_pdf_not_completed():
    """PDF download returns 409 when job is not completed."""
    job = SimpleNamespace(
        job_id="job-1",
        status="processing",
        manifest=None,
        pptx_storage_path=None,
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/pdf")
    assert response.status_code == 409
    assert response.headers.get("retry-after") == "5"
    data = response.json()
    assert data["error"]["message"] == "Deck is still being generated"
    assert data["error"]["code"] == "DECK_NOT_READY"
    assert data["error"]["status"] == 409
    assert data["error"]["job_status"] == "processing"


# ---------------------------------------------------------------------------
# download_slide_preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_slide_preview_not_found():
    """Slide preview download returns 404 when job does not exist."""
    app = _make_app(row=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/no-job/slides/0.png")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_slide_preview_not_completed():
    """Slide preview returns 409 when job is not completed."""
    job = SimpleNamespace(
        job_id="job-1",
        status="processing",
        manifest=None,
        pptx_storage_path=None,
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/slides/0.png")
    assert response.status_code == 409
    assert response.headers.get("retry-after") == "5"
    data = response.json()
    assert data["error"]["message"] == "Deck is still being generated"
    assert data["error"]["code"] == "DECK_NOT_READY"
    assert data["error"]["status"] == 409
    assert data["error"]["job_status"] == "processing"


@pytest.mark.asyncio
async def test_download_slide_preview_fallback_path(tmp_path: Path):
    """Slide preview uses fallback path when manifest has no slide assets."""
    preview_path = tmp_path / "decks" / "job-1" / "slides" / "slide_01.png"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    job = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={"assets": {"slides": []}},  # empty slides list → fallback
        pptx_storage_path=None,
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/slides/0.png")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_download_slide_preview_storage_miss():
    """Slide preview returns 404 when the file is not in storage."""
    job = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={"assets": {}},
        pptx_storage_path=None,
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/slides/0.png")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# get_manifest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_manifest_no_manifest():
    """Manifest endpoint returns 404 when row.manifest is None."""
    job = SimpleNamespace(
        job_id="j-1",
        status="completed",
        manifest=None,
    )
    app = _make_app(row=job)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/j-1/manifest")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# _get_storage helper
# ---------------------------------------------------------------------------


def test_get_storage_creates_and_caches():
    """_get_storage creates StorageService when not on app.state."""
    app_state = SimpleNamespace()
    app = SimpleNamespace(state=app_state)
    request = cast(Request, SimpleNamespace(app=app))

    storage1 = _get_storage(request)
    storage2 = _get_storage(request)
    assert storage1 is storage2  # same instance cached


def test_get_storage_returns_existing():
    """_get_storage returns pre-set storage_service."""
    existing = MagicMock()
    app_state = SimpleNamespace(storage_service=existing)
    app = SimpleNamespace(state=app_state)
    request = cast(Request, SimpleNamespace(app=app))

    result = _get_storage(request)
    assert result is existing


# ---------------------------------------------------------------------------
# create_deck (202 accepted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deck_returns_202():
    """POST /api/decks returns 202 with job_id."""
    deck_builder = MagicMock()
    deck_builder.prepare_templates = AsyncMock(return_value=({}, {}))
    deck_builder.build_deck = AsyncMock(return_value={"slide_count": 1})

    app = _make_app(deck_builder=deck_builder)

    with patch("src.api.routes.get_db_session") as mock_session_ctx:
        # get_db_session is used in the background task; mock it to avoid real DB
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=_StubDB())
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session_ctx.return_value = mock_db

        with (
            patch("src.api.routes.acquire_job_lock", new_callable=AsyncMock, return_value=True),
            patch("src.api.routes.release_job_lock", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/decks",
                    json={
                        "title": "Test",
                        "slides": [{"template_id": "t-1", "content": {"title": "Hi"}}],
                    },
                )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "processing"
    assert "job_id" in data


@pytest.mark.asyncio
async def test_create_deck_rejects_an_empty_slide_list():
    """POST /api/decks with ``slides: []`` must be a 422, not a 202.

    ``slides`` carried no ``min_length``, so an empty list was accepted, the job
    reached ``completed``, and the service emitted an empty PPTX/PDF plus a
    manifest with zero assets — a successful-looking deck containing nothing.
    """
    deck_builder = MagicMock()
    deck_builder.prepare_templates = AsyncMock(return_value=({}, {}))
    deck_builder.build_deck = AsyncMock(return_value={"slide_count": 0})

    app = _make_app(deck_builder=deck_builder)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/decks", json={"title": "Empty", "slides": []})

    assert response.status_code == 422
    assert deck_builder.build_deck.await_count == 0


def test_deck_create_request_rejects_empty_slides():
    """Schema level: the constraint lives on the model, not only on the route."""
    from pydantic import ValidationError

    from src.api.schemas import DeckCreateRequest

    with pytest.raises(ValidationError):
        DeckCreateRequest(title="Empty", slides=[])


async def test_process_deck_reestablishes_correlation_context(monkeypatch):
    """The detached deck task re-establishes cost-correlation (run_id + stage).

    Without this, the WM LLM call the task makes emits a cost_event with
    correlation_id NULL and billing can't tie the spend back to the run. The
    enqueue snapshots correlation in request scope and passes it in; the task
    calls set_correlation_context() before any WM call.
    """
    from w2v_common.auth import (
        clear_identity_context,
        get_correlation_id_from_context,
        get_stage_from_context,
    )

    from src.api import routes

    seen: dict[str, str] = {}

    async def fake_lock(_redis, _job_id):
        # Runs after set_correlation_context in _process_deck; capture + early-exit.
        seen["correlation_id"] = get_correlation_id_from_context()
        seen["stage"] = get_stage_from_context()
        return False

    monkeypatch.setattr(routes, "acquire_job_lock", fake_lock)
    clear_identity_context()

    await routes._process_deck(
        MagicMock(),
        "job-1",
        {},
        MagicMock(),
        None,
        # A real tenant uuid: `_process_deck` re-establishes identity via
        # `set_identity_context`, which parses the tenant and raises
        # InvalidTenantIdError on a slug (w2v-common 0.67.0).
        "0195f48b-d000-7000-8000-0000000000ab",
        "run-xyz",
        "slides",
    )

    assert seen["correlation_id"] == "run-xyz"
    assert seen["stage"] == "slides"
