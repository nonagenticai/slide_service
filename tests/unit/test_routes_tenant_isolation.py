"""Cross-tenant isolation tests for slide_service API routes.

These tests use the real Postgres CI database (via the ``db_engine`` /
``db_session`` fixtures in conftest.py) so the tenant filter actually
runs at the SQL level rather than being short-circuited by stub DB
sessions. Each test seeds rows for two tenants (:data:`TENANT_A` and
:data:`TENANT_B`, real uuids — ``tenant_id`` is a native ``uuid`` column)
and asserts that requests carrying tenant B's identity cannot read or
mutate tenant A's data.

Platform templates are ``is_system = True`` rows owned by an ORDINARY tenant
uuid (:data:`SYSTEM_OWNER`). The ``tenant_id = 'system'`` sentinel is retired;
cross-tenant visibility comes from the flag alone, which is why the suite also
seeds a NON-system row owned by :data:`SYSTEM_OWNER` and proves it stays
hidden.

The auth middleware is NOT installed in these tests; instead we attach a
tiny pre-router middleware that copies an ``X-Test-Tenant`` header into
``request.state.tenant_id`` -- the same attribute the real middleware
sets. ``_get_tenant_id`` reads from that attribute, and raises 401 (never a
``"default"`` sentinel) when it is absent — proven by
``test_missing_tenant_returns_401``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes import router
from src.database.models import DeckJob, SlideTemplate
from src.database.session import get_db

TENANT_A = "0195f48b-d000-7000-8000-0000000000aa"
TENANT_B = "0195f48b-d000-7000-8000-0000000000bb"
#: Owns every platform template. A real registered tenant, NOT a sentinel.
SYSTEM_OWNER = "0195f48b-d000-7000-8000-000000000099"


def _make_app(db_session_factory) -> FastAPI:
    """Build a test app whose ``get_db`` yields real AsyncSessions and which
    forwards ``X-Test-Tenant`` to ``request.state.tenant_id``.
    """
    app = FastAPI(title="slide-service-tenant-test")

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
        slot_definitions={"title": {"type": "text"}},
        styling_rules={},
        default_transition=None,
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
    status: str = "completed",
) -> DeckJob:
    job = DeckJob(
        job_id=job_id,
        tenant_id=tenant_id,
        status=status,
        request_payload={"title": "Test"},
        manifest={
            "manifest_version": "1.0",
            "deck_id": job_id,
            "title": "Test",
            "aspect_ratio": "16:9",
            "slide_count": 1,
            "total_estimated_duration_seconds": 10.0,
            "brand": {},
            "assets": {},
            "slides": [],
        },
        pptx_storage_path=f"decks/{job_id}/deck.pptx",
        slide_count=1,
    )
    session.add(job)
    await session.commit()
    return job


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_template_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot read tenant A's template."""
    await _seed_template(db_session, template_id="tpl-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/templates/tpl-a", headers={"X-Test-Tenant": TENANT_B})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_template_owner_returns_200(db_session, db_session_factory):
    """The owning tenant can still read their own template."""
    await _seed_template(db_session, template_id="tpl-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/templates/tpl-a", headers={"X-Test-Tenant": TENANT_A})
    assert response.status_code == 200
    assert response.json()["id"] == "tpl-a"


@pytest.mark.asyncio
async def test_get_template_system_visible_to_all(db_session, db_session_factory):
    """``is_system`` templates are visible to EVERY tenant — both of them, and
    the platform tenant's own non-system row is NOT.
    """
    await _seed_template(db_session, template_id="tpl-sys", tenant_id=SYSTEM_OWNER, is_system=True)
    await _seed_template(
        db_session, template_id="tpl-priv", tenant_id=SYSTEM_OWNER, is_system=False
    )

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for tenant in (TENANT_A, TENANT_B):
            response = await client.get("/api/templates/tpl-sys", headers={"X-Test-Tenant": tenant})
            assert response.status_code == 200, (tenant, response.status_code)
            assert response.json()["is_system"] is True
            # Same owner, is_system=False → must stay private to SYSTEM_OWNER.
            leak = await client.get("/api/templates/tpl-priv", headers={"X-Test-Tenant": tenant})
            assert leak.status_code == 404, (tenant, leak.status_code)


@pytest.mark.asyncio
async def test_missing_tenant_returns_401(db_session, db_session_factory):
    """No tenant identity is a 401 — ``_get_tenant_id`` no longer defaults to
    ``"default"``, so an unauthenticated caller can never read anything.
    """
    await _seed_template(db_session, template_id="tpl-sys", tenant_id=SYSTEM_OWNER, is_system=True)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Not even the globally-readable is_system row is served without identity.
        assert (await client.get("/api/templates/tpl-sys")).status_code == 401
        assert (await client.get("/api/templates")).status_code == 401


@pytest.mark.asyncio
async def test_list_templates_excludes_other_tenants(db_session, db_session_factory):
    """List endpoint hides other tenants' rows and shows every ``is_system`` row."""
    await _seed_template(db_session, template_id="tpl-a", tenant_id=TENANT_A)
    await _seed_template(db_session, template_id="tpl-b", tenant_id=TENANT_B)
    await _seed_template(db_session, template_id="tpl-sys", tenant_id=SYSTEM_OWNER, is_system=True)
    await _seed_template(
        db_session, template_id="tpl-priv", tenant_id=SYSTEM_OWNER, is_system=False
    )

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/templates", headers={"X-Test-Tenant": TENANT_B})
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()["templates"]}
    assert ids == {"tpl-b", "tpl-sys"}
    # Both halves, stated separately so a regression names itself.
    assert "tpl-sys" in ids, "is_system template missing from the list for tenant B"
    assert "tpl-a" not in ids, "tenant A's template leaked into tenant B's list"
    assert "tpl-priv" not in ids, "non-system row leaked because SYSTEM_OWNER owns it"


@pytest.mark.asyncio
async def test_update_template_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot update tenant A's template."""
    await _seed_template(db_session, template_id="tpl-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/templates/tpl-a",
            json={"display_name": "Hijacked"},
            headers={"X-Test-Tenant": TENANT_B},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_template_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot soft-delete tenant A's template."""
    await _seed_template(db_session, template_id="tpl-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/templates/tpl-a", headers={"X-Test-Tenant": TENANT_B})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_template_versions_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot enumerate tenant A's template versions."""
    await _seed_template(db_session, template_id="tpl-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/templates/tpl-a/versions", headers={"X-Test-Tenant": TENANT_B}
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deck_status_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot read tenant A's deck job status."""
    await _seed_deck_job(db_session, job_id="job-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-a", headers={"X-Test-Tenant": TENANT_B})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_manifest_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot read tenant A's manifest."""
    await _seed_deck_job(db_session, job_id="job-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/decks/job-a/manifest", headers={"X-Test-Tenant": TENANT_B}
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_pptx_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot download tenant A's PPTX -- the fallback storage path
    must NOT be reachable for non-owners.
    """
    await _seed_deck_job(db_session, job_id="job-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-a/pptx", headers={"X-Test-Tenant": TENANT_B})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_pdf_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot download tenant A's PDF."""
    await _seed_deck_job(db_session, job_id="job-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/decks/job-a/pdf", headers={"X-Test-Tenant": TENANT_B})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_slide_preview_cross_tenant_returns_404(db_session, db_session_factory):
    """Tenant B cannot download tenant A's slide preview PNG."""
    await _seed_deck_job(db_session, job_id="job-a", tenant_id=TENANT_A)

    app = _make_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/decks/job-a/slides/0.png", headers={"X-Test-Tenant": TENANT_B}
        )
    assert response.status_code == 404
