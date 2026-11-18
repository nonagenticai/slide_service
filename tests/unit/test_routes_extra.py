"""Extra route tests for template CRUD and helpers coverage."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from src.api.routes import (
    TaskContext,
    _build_artifact_response,
    _cache_enabled,
    _get_redis,
    _get_tenant_id,
    _resolve_asset_storage_path,
    router,
)
from src.config import clear_settings_cache, get_settings
from src.database.session import get_db
from src.services.storage_service import StorageService

#: These tests stub the DB session, so the tenant filter is not what they exercise
#: -- but ``_get_tenant_id`` now raises 401 when no identity is on the request
#: (the ``"default"`` sentinel is retired), so every request needs a real tenant
#: uuid. ``tests/unit/test_routes_tenant_isolation`` owns the proof that the
#: filter itself works, and ``test_missing_tenant_returns_401`` there owns the
#: proof that an identity-less request is rejected.
TEST_TENANT = "0195f48b-d000-7000-8000-0000000000aa"


class _ExecuteResult:
    def __init__(self, row: object | None = None, rows: Sequence[object] | None = None) -> None:
        self._row = row
        self._rows: list[object] = list(rows) if rows else []

    def scalar_one_or_none(self) -> object | None:
        return self._row

    def scalar(self) -> int | None:
        return len(self._rows)

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._rows)


class _ScalarsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _StubDBSession:
    def __init__(self, row: object | None = None, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = rows or []
        self._added: list[object] = []

    async def execute(self, _stmt: object) -> _ExecuteResult:
        return _ExecuteResult(self._row, self._rows)

    def add(self, obj: object) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


class _StubStorage:
    def __init__(
        self, *, backend_type: str = "local", files: dict[str, bytes] | None = None
    ) -> None:
        self.backend_type = backend_type
        self._files = files or {}

    async def exists(self, relative_path: str) -> bool:
        return relative_path in self._files

    async def read_file(self, relative_path: str) -> bytes:
        return self._files[relative_path]

    def get_full_path(self, relative_path: str) -> str:
        raise NotImplementedError


def _make_test_app(
    row: object | None = None,
    rows: list[object] | None = None,
    *,
    storage: object | None = None,
) -> FastAPI:
    app = FastAPI(title="test")

    async def override_db() -> AsyncGenerator[_StubDBSession]:
        yield _StubDBSession(row, rows)

    app.dependency_overrides[get_db] = override_db
    if storage is not None:
        app.state.storage_service = storage

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


# ---- _resolve_asset_storage_path ----


class TestResolveAssetStoragePath:
    def test_extracts_path_from_manifest_dict_asset(self) -> None:
        """When asset is a dict with storage_path, returns it."""
        manifest = {
            "assets": {
                "pptx": {"storage_path": "decks/j1/deck.pptx"},
            }
        }
        result = _resolve_asset_storage_path(manifest, "pptx", "fallback")
        assert result == "decks/j1/deck.pptx"

    def test_extracts_path_from_manifest_string_asset(self) -> None:
        """When asset is a plain string, returns it directly."""
        manifest = {"assets": {"pdf": "decks/j1/deck.pdf"}}
        result = _resolve_asset_storage_path(manifest, "pdf", "fallback")
        assert result == "decks/j1/deck.pdf"

    def test_returns_fallback_when_asset_key_missing(self) -> None:
        """When asset key is not in manifest.assets, returns fallback."""
        manifest = {"assets": {}}
        result = _resolve_asset_storage_path(manifest, "pptx", "decks/j1/fallback.pptx")
        assert result == "decks/j1/fallback.pptx"

    def test_returns_fallback_when_no_assets_key(self) -> None:
        """When manifest has no assets key at all, returns fallback."""
        result = _resolve_asset_storage_path({}, "pptx", "fallback.pptx")
        assert result == "fallback.pptx"

    def test_returns_fallback_when_manifest_is_none(self) -> None:
        """When manifest is None, returns fallback."""
        result = _resolve_asset_storage_path(None, "pptx", "fallback.pptx")
        assert result == "fallback.pptx"


# ---- _get_tenant_id ----


class TestGetTenantId:
    def test_raises_401_when_not_set(self) -> None:
        """No tenant identity is an ERROR, never a "default" sentinel."""
        request = cast(Request, SimpleNamespace(state=SimpleNamespace()))
        with pytest.raises(HTTPException) as exc:
            _get_tenant_id(request)
        assert exc.value.status_code == 401

    def test_raises_401_when_blank(self) -> None:
        """A whitespace-only tenant_id is as absent as none at all."""
        request = cast(Request, SimpleNamespace(state=SimpleNamespace(tenant_id="   ")))
        with pytest.raises(HTTPException) as exc:
            _get_tenant_id(request)
        assert exc.value.status_code == 401

    def test_returns_tenant_id_when_set(self) -> None:
        """When request.state has a real tenant uuid, returns it."""
        tenant = "0195f48b-d000-7000-8000-0000000000aa"
        request = cast(Request, SimpleNamespace(state=SimpleNamespace(tenant_id=tenant)))
        result = _get_tenant_id(request)
        assert result == tenant

    def test_raises_401_when_not_a_uuid(self) -> None:
        """A legacy slug tenant is refused: tenant_id is a native uuid column."""
        request = cast(Request, SimpleNamespace(state=SimpleNamespace(tenant_id="my-tenant")))
        with pytest.raises(HTTPException) as exc:
            _get_tenant_id(request)
        assert exc.value.status_code == 401

    def test_401_detail_never_echoes_the_rejected_value(self) -> None:
        """X-Tenant-Id can carry `Bearer <license token>`; the 401 must not leak it."""
        secret = (
            "Bearer eyJhbGciOiJIUzI1NiJ9.super-secret-license-token"  # pragma: allowlist secret
        )
        request = cast(Request, SimpleNamespace(state=SimpleNamespace(tenant_id=secret)))
        with pytest.raises(HTTPException) as exc:
            _get_tenant_id(request)
        assert exc.value.status_code == 401
        assert "super-secret-license-token" not in str(exc.value.detail)

    @pytest.mark.parametrize(
        "nil_spelling",
        [
            "00000000-0000-0000-0000-000000000000",
            "00000000000000000000000000000000",
            "{00000000-0000-0000-0000-000000000000}",
            "urn:uuid:00000000-0000-0000-0000-000000000000",
            "  00000000-0000-0000-0000-000000000000  ",
        ],
    )
    def test_raises_401_on_every_spelling_of_the_nil_uuid(self, nil_spelling: str) -> None:
        """The nil uuid is rejected BY VALUE, not by string-comparing one spelling.

        It was w2v-common's retired fail-safe substitute for an absent tenant and
        is a *valid* uuid, so nothing else in the stack stops it. A guard that
        compared against the dashed literal would let the four other spellings
        through and write mis-attributed rows under `default-anon`.
        """
        request = cast(Request, SimpleNamespace(state=SimpleNamespace(tenant_id=nil_spelling)))
        with pytest.raises(HTTPException) as exc:
            _get_tenant_id(request)
        assert exc.value.status_code == 401

    def test_normalises_an_undashed_tenant_to_canonical_form(self) -> None:
        """One tenant must not become two keys because of inbound spelling."""
        request = cast(
            Request,
            SimpleNamespace(state=SimpleNamespace(tenant_id="0195f48bd000700080000000000000aa")),
        )
        assert _get_tenant_id(request) == "0195f48b-d000-7000-8000-0000000000aa"


# ---- _get_redis ----


class TestGetRedis:
    def test_returns_none_when_redis_not_configured(self) -> None:
        """When app.state has no redis_client, returns None."""
        request = cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
        result = _get_redis(request)
        assert result is None

    def test_returns_redis_when_configured(self) -> None:
        """When app.state has redis_client, returns it."""
        sentinel = object()
        request = cast(
            Request,
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_client=sentinel))),
        )
        result = _get_redis(request)
        assert result is sentinel


# ---- _cache_enabled ----


class TestCacheEnabled:
    def test_returns_bool_matching_settings(self) -> None:
        """cache_enabled() returns the value from settings.cache.enabled."""
        result = _cache_enabled()
        settings = get_settings()
        assert result is settings.cache.enabled
        assert isinstance(result, bool)


# ---- TaskContext ----


class TestTaskContext:
    def test_stores_all_fields(self) -> None:
        """TaskContext dataclass stores job_id, tenant_id, and request_id."""
        ctx = TaskContext(job_id="j1", tenant_id="t1", request_id="r1")
        assert ctx.job_id == "j1"
        assert ctx.tenant_id == "t1"
        assert ctx.request_id == "r1"

    def test_fields_are_independent(self) -> None:
        """Different TaskContext instances are independent."""
        ctx1 = TaskContext(job_id="a", tenant_id="b", request_id="c")
        ctx2 = TaskContext(job_id="x", tenant_id="y", request_id="z")
        assert ctx1.job_id != ctx2.job_id
        assert ctx1.tenant_id != ctx2.tenant_id
        assert ctx1.request_id != ctx2.request_id


# ---- _build_artifact_response ----


class TestBuildArtifactResponse:
    @pytest.mark.asyncio
    async def test_returns_404_when_artifact_missing(self) -> None:
        """Returns HTTP 404 when storage says file doesn't exist."""
        storage = cast(StorageService, _StubStorage(files={}))
        with pytest.raises(HTTPException) as exc_info:
            await _build_artifact_response(
                storage=storage,
                storage_path="missing/file.pdf",
                media_type="application/pdf",
            )
        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_minio_backend_returns_bytes_with_content_type(self) -> None:
        """MinIO backend reads file bytes and returns them with correct headers."""
        pdf_bytes = b"%PDF-1.7\nfake content"
        storage = cast(
            StorageService,
            _StubStorage(
                backend_type="minio",
                files={"decks/j1/deck.pdf": pdf_bytes},
            ),
        )
        response = await _build_artifact_response(
            storage=storage,
            storage_path="decks/j1/deck.pdf",
            media_type="application/pdf",
            filename="test.pdf",
        )
        assert response.body == pdf_bytes
        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"] == 'attachment; filename="test.pdf"'

    @pytest.mark.asyncio
    async def test_minio_backend_without_filename_omits_disposition(self) -> None:
        """MinIO backend without filename does not set content-disposition."""
        storage = cast(
            StorageService,
            _StubStorage(
                backend_type="minio",
                files={"decks/j1/slide.png": b"PNG"},
            ),
        )
        response = await _build_artifact_response(
            storage=storage,
            storage_path="decks/j1/slide.png",
            media_type="image/png",
        )
        assert response.body == b"PNG"
        assert response.media_type == "image/png"
        assert "content-disposition" not in response.headers


# ---- Template version listing ----


@pytest.mark.asyncio
async def test_list_template_versions_returns_version_details() -> None:
    """GET /api/templates/:id/versions returns version list with all fields."""
    versions = [
        SimpleNamespace(
            version_id="v-1",
            template_id="t-1",
            version_number=1,
            snapshot_hash="abc123",
            slot_definitions={"title": {"type": "text"}},
            styling_rules={},
            default_transition=None,
            default_timing_hint_seconds=10.0,
            created_at=None,
        ),
    ]

    class _VersionDB:
        def __init__(self) -> None:
            self._call = 0

        async def execute(self, _stmt: object) -> _ExecuteResult:
            self._call += 1
            # First call: parent SlideTemplate ownership check (scalar_one_or_none).
            # Return any non-None scalar so the route proceeds.
            if self._call == 1:
                return _ExecuteResult(row=1)
            # Subsequent calls: TemplateVersion list.
            return _ExecuteResult(rows=versions)

    app = FastAPI(title="test")

    async def override_db() -> AsyncGenerator[_VersionDB]:
        yield _VersionDB()

    app.dependency_overrides[get_db] = override_db

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant_id = TEST_TENANT
        return await call_next(request)

    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/templates/t-1/versions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["version_id"] == "v-1"
    assert data[0]["template_id"] == "t-1"
    assert data[0]["version_number"] == 1
    assert data[0]["snapshot_hash"] == "abc123"
    assert data[0]["slot_definitions"] == {"title": {"type": "text"}}
    assert data[0]["default_timing_hint_seconds"] == 10.0
