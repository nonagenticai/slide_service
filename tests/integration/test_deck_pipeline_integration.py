"""Real HTTP + real-Postgres integration tests for slide_service.

Unlike the stubbed unit tests in ``tests/unit``, these drive the actual
FastAPI router over ``httpx.ASGITransport`` against the CI Postgres database
(the ``db_engine`` / ``db_session`` / ``db_session_factory`` fixtures in
``tests/conftest.py``) so the SQL, the persistence, and the async
deck-generation background task all run for real.

The auth middleware is not installed; a tiny pre-router middleware copies an
``X-Test-Tenant`` header into ``request.state.tenant_id`` -- the same attribute
``_get_tenant_id`` reads (matching ``tests/unit/test_routes_tenant_isolation``).
``tenant_id`` is a native ``uuid`` column, so the two tenants below are real
uuids -- the former ``"tenant-a"`` / ``"tenant-b"`` literals are no longer
writable values.

The only things stubbed are the two heavy external renderers that the deck
pipeline shells out to -- headless Chromium (``render_slide_png``) and
LibreOffice (``convert_pptx_to_pdf``). Everything else (python-pptx build,
manifest build, local StorageService I/O, DB writes from the detached
background task) executes against real infrastructure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from w2v_common.clients import WMResponse

from src.api.routes import router
from src.config import clear_settings_cache, get_settings
from src.database.models import DeckJob, SlideTemplate
from src.database.session import get_db
from src.services.deck_builder import DeckBuilder
from src.services.storage_service import StorageService

TENANT_A = "0195f48b-d000-7000-8000-0000000000aa"
TENANT_B = "0195f48b-d000-7000-8000-0000000000bb"
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# ---------------------------------------------------------------------------
# App / storage wiring helpers
# ---------------------------------------------------------------------------


def _make_app(db_session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    """Build a test app whose ``get_db`` yields real AsyncSessions and which
    forwards ``X-Test-Tenant`` to ``request.state.tenant_id``.
    """
    app = FastAPI(title="slide-service-integration-test")

    async def override_db() -> AsyncGenerator[AsyncSession]:
        async with db_session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_db

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        tenant = request.headers.get("X-Test-Tenant")
        if tenant:
            request.state.tenant_id = tenant
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _local_storage_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None]:
    """Route StorageService at a local temp dir for the duration of a test."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()
    yield
    clear_settings_cache()


async def _make_storage() -> StorageService:
    storage = StorageService()
    await storage.initialize()
    return storage


# ---------------------------------------------------------------------------
# Seed helpers (real rows via a real session)
# ---------------------------------------------------------------------------


async def _seed_template(
    session: AsyncSession,
    *,
    template_id: str,
    tenant_id: str,
    is_system: bool = False,
) -> SlideTemplate:
    template = SlideTemplate(
        template_id=template_id,
        tenant_id=tenant_id,
        name=f"name-{template_id}",
        display_name=f"Display {template_id}",
        category="narrative",
        layout_type="title",
        slot_definitions={"title": {"type": "text"}, "subtitle": {"type": "text"}},
        styling_rules={"layout": "centered"},
        default_transition={"type": "fade", "duration_seconds": 0.5},
        default_timing_hint_seconds=10.0,
        is_system=is_system,
        is_active=True,
    )
    session.add(template)
    await session.commit()
    return template


async def _seed_deck_job(
    session: AsyncSession,
    *,
    job_id: str,
    tenant_id: str,
    status: str,
    pptx_storage_path: str | None = None,
    manifest: dict | None = None,
) -> DeckJob:
    job = DeckJob(
        job_id=job_id,
        tenant_id=tenant_id,
        status=status,
        request_payload={"title": "Test"},
        manifest=manifest,
        pptx_storage_path=pptx_storage_path,
        slide_count=1 if manifest else None,
    )
    session.add(job)
    await session.commit()
    return job


async def _poll_until_terminal(
    client: AsyncClient,
    job_id: str,
    tenant: str,
    *,
    timeout_s: float = 8.0,
) -> str:
    """Poll GET /api/decks/{job_id} until the job leaves 'processing'."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    status = "processing"
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/decks/{job_id}", headers={"X-Test-Tenant": tenant})
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status != "processing":
            return status
        await asyncio.sleep(0.1)
    return status


# ---------------------------------------------------------------------------
# Test 1: Template CRUD real-DB round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_crud_round_trip(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    sample_template_data: dict,
) -> None:
    """POST -> PUT -> DELETE round-trips are each verified against the DB row,
    and a cross-tenant GET is rejected with 404.
    """
    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # POST -> 201, row persisted.
        create = await client.post(
            "/api/templates", json=sample_template_data, headers={"X-Test-Tenant": TENANT_A}
        )
        assert create.status_code == 201
        template_id = create.json()["id"]

        async with db_session_factory() as verify:
            row = (
                await verify.execute(
                    select(SlideTemplate).where(SlideTemplate.template_id == template_id)
                )
            ).scalar_one()
            assert row.tenant_id == TENANT_A
            assert row.display_name == "Test Template"
            assert row.is_active is True

        # PUT -> 200, the change is persisted in the DB row.
        update = await client.put(
            f"/api/templates/{template_id}",
            json={"display_name": "Renamed Template"},
            headers={"X-Test-Tenant": TENANT_A},
        )
        assert update.status_code == 200
        async with db_session_factory() as verify:
            row = (
                await verify.execute(
                    select(SlideTemplate).where(SlideTemplate.template_id == template_id)
                )
            ).scalar_one()
            assert row.display_name == "Renamed Template"

        # The versions endpoint is reachable for the owner. A plain PUT does NOT
        # auto-version (versioning is a deck-build side effect in DeckBuilder.
        # _freeze_versions, not a route side effect), so the list is empty here.
        versions = await client.get(
            f"/api/templates/{template_id}/versions", headers={"X-Test-Tenant": TENANT_A}
        )
        assert versions.status_code == 200
        assert versions.json() == []

        # Cross-tenant GET is a 404 (existence not disclosed).
        cross = await client.get(
            f"/api/templates/{template_id}", headers={"X-Test-Tenant": TENANT_B}
        )
        assert cross.status_code == 404

        # DELETE -> soft-delete; the DB row flips is_active=False.
        delete = await client.delete(
            f"/api/templates/{template_id}", headers={"X-Test-Tenant": TENANT_A}
        )
        assert delete.status_code == 200
        assert delete.json()["deleted"] is True
        async with db_session_factory() as verify:
            row = (
                await verify.execute(
                    select(SlideTemplate).where(SlideTemplate.template_id == template_id)
                )
            ).scalar_one()
            assert row.is_active is False


# ---------------------------------------------------------------------------
# Test 2: POST /api/decks -> GET happy path (real background task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deck_completes_and_is_readable(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    sample_deck_request: dict,
) -> None:
    """POST returns 202; the detached background task builds the deck (with the
    two heavy renderers stubbed) and drives the DeckJob row to 'completed',
    which GET then reports.
    """
    await _seed_template(db_session, template_id="test-template-id", tenant_id=TENANT_A)

    storage = await _make_storage()
    builder = DeckBuilder(get_settings(), storage_service=storage)
    app = _make_app(db_session_factory)
    app.state.deck_builder = builder
    app.state.storage_service = storage

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(
            "src.services.deck_builder.render_slide_png",
            AsyncMock(return_value=b"\x89PNG\r\n\x1a\n"),
        )
        mp.setattr(
            "src.services.deck_builder.convert_pptx_to_pdf",
            AsyncMock(return_value=b"%PDF-1.7 fake"),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/decks", json=sample_deck_request, headers={"X-Test-Tenant": TENANT_A}
            )
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            status = await _poll_until_terminal(client, job_id, TENANT_A)
            assert status == "completed", f"job ended in {status!r}, expected completed"

            get_resp = await client.get(f"/api/decks/{job_id}", headers={"X-Test-Tenant": TENANT_A})
            assert get_resp.status_code == 200
            assert get_resp.json()["status"] == "completed"

    # The persisted DB row is terminal + carries a manifest.
    async with db_session_factory() as verify:
        row = (await verify.execute(select(DeckJob).where(DeckJob.job_id == job_id))).scalar_one()
        assert row.status == "completed"
        assert row.manifest is not None
        assert row.manifest["slide_count"] == 1


# ---------------------------------------------------------------------------
# Test 3: Artifact download state machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pptx_download_state_machine(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The pptx download endpoint returns 409 while processing, 422 on failure,
    and 200 with the correct media type once completed with a real file on disk.
    """
    storage = await _make_storage()
    app = _make_app(db_session_factory)
    app.state.storage_service = storage

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # processing -> 409
        await _seed_deck_job(db_session, job_id="job-proc", tenant_id=TENANT_A, status="processing")
        r409 = await client.get("/api/decks/job-proc/pptx", headers={"X-Test-Tenant": TENANT_A})
        assert r409.status_code == 409
        assert r409.headers.get("retry-after") == "5"

        # failed -> 422
        await _seed_deck_job(db_session, job_id="job-fail", tenant_id=TENANT_A, status="failed")
        r422 = await client.get("/api/decks/job-fail/pptx", headers={"X-Test-Tenant": TENANT_A})
        assert r422.status_code == 422

        # completed with a real file -> 200 + pptx media type
        storage_path = "decks/job-done/deck.pptx"
        await storage.write_file(
            storage_path, b"PK\x03\x04 fake-pptx", content_type=PPTX_MEDIA_TYPE
        )
        await _seed_deck_job(
            db_session,
            job_id="job-done",
            tenant_id=TENANT_A,
            status="completed",
            pptx_storage_path=storage_path,
            manifest={
                "manifest_version": "1.0",
                "deck_id": "job-done",
                "title": "Done",
                "aspect_ratio": "16:9",
                "slide_count": 1,
                "total_estimated_duration_seconds": 10.0,
                "brand": {},
                "assets": {},
                "slides": [],
            },
        )
        r200 = await client.get("/api/decks/job-done/pptx", headers={"X-Test-Tenant": TENANT_A})
        assert r200.status_code == 200
        assert r200.headers["content-type"].startswith(PPTX_MEDIA_TYPE)
        assert r200.content == b"PK\x03\x04 fake-pptx"


# ---------------------------------------------------------------------------
# Test 4: content-shaper wired through POST /api/decks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_deck_with_content_shaper(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    sample_deck_request: dict,
) -> None:
    """With content_shaper.enabled=True and an injected AsyncMock WM client, the
    deck pipeline calls WM and the persisted manifest reflects the shaped
    content -- proving the pipeline actually routes through worker_management.
    """
    await _seed_template(db_session, template_id="test-template-id", tenant_id=TENANT_A)

    wm = AsyncMock()
    wm.message = AsyncMock(
        return_value=WMResponse(
            output={
                "slides": [
                    {
                        "index": 0,
                        "content": {"title": "SHAPED-TITLE", "subtitle": "shaped-sub"},
                        "speaker_notes": "shaped notes",
                    }
                ]
            },
            output_type="object",
            output_schema_used=None,
            metadata=None,
            usage_metrics=None,
        )
    )

    storage = await _make_storage()
    builder = DeckBuilder(get_settings(), storage_service=storage, wm_client=wm)
    app = _make_app(db_session_factory)
    app.state.deck_builder = builder
    app.state.storage_service = storage

    shaper_request = {**sample_deck_request, "content_shaper": {"enabled": True}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.services.deck_builder.render_slide_png",
            AsyncMock(return_value=b"\x89PNG\r\n\x1a\n"),
        )
        mp.setattr(
            "src.services.deck_builder.convert_pptx_to_pdf",
            AsyncMock(return_value=b"%PDF-1.7 fake"),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/decks", json=shaper_request, headers={"X-Test-Tenant": TENANT_A}
            )
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            status = await _poll_until_terminal(client, job_id, TENANT_A)
            assert status == "completed", f"job ended in {status!r}, expected completed"

    # WM was actually called, and the shaped title survived into the manifest.
    wm.message.assert_awaited()
    async with db_session_factory() as verify:
        row = (await verify.execute(select(DeckJob).where(DeckJob.job_id == job_id))).scalar_one()
        assert row.status == "completed"
        manifest = row.manifest
        assert manifest is not None
        titles = [s.get("content", {}).get("title") for s in manifest["slides"]]
        assert "SHAPED-TITLE" in titles
