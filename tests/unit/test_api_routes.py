"""Focused API route tests for deck artifact delivery."""

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
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _StubDBSession:
    def __init__(self, row: object | None) -> None:
        self._row = row

    async def execute(self, _stmt: object) -> _ExecuteResult:
        return _ExecuteResult(self._row)


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


def _make_test_app(row: object | None, *, storage: object | None = None) -> FastAPI:
    app = FastAPI(title="slide-service-test")

    async def override_db() -> AsyncGenerator[_StubDBSession]:
        yield _StubDBSession(row)

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
def _storage_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[Path]:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()
    yield tmp_path
    clear_settings_cache()


@pytest.mark.asyncio
async def test_download_pdf_returns_generated_file(tmp_path: Path):
    pdf_path = tmp_path / "decks" / "job-1" / "deck.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")

    row = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={"assets": {"pdf": {"storage_path": "decks/job-1/deck.pdf"}}},
        pptx_storage_path=None,
    )
    app = _make_test_app(row)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/pdf")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.7\n"
    assert response.headers["content-type"].startswith("application/pdf")


@pytest.mark.asyncio
async def test_download_slide_preview_is_inline(tmp_path: Path):
    preview_path = tmp_path / "decks" / "job-1" / "slides" / "slide_01.png"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    row = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={
            "assets": {"slides": [{"index": 0, "storage_path": "decks/job-1/slides/slide_01.png"}]}
        },
        pptx_storage_path=None,
    )
    app = _make_test_app(row)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/slides/0.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
async def test_download_pdf_streams_from_minio_style_storage():
    row = SimpleNamespace(
        job_id="job-1",
        status="completed",
        manifest={"assets": {"pdf": {"storage_path": "decks/job-1/deck.pdf"}}},
        pptx_storage_path=None,
    )
    app = _make_test_app(
        row,
        storage=_StubStorage(
            backend_type="minio",
            files={"decks/job-1/deck.pdf": b"%PDF-1.7\n"},
        ),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/decks/job-1/pdf")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.7\n"
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == 'attachment; filename="job-1.pdf"'
