"""Guardrail tests for the auth middleware integration.

slide_service delegates auth to ``w2v_common.auth.middleware.AuthMiddleware``.
This test exercises that the middleware is **wired and registered** on the
asgi app (not merely importable), and that it behaves correctly for the
standard public / protected / invalid-token matrix.

The intent is to catch the "false-merge" pattern: a PR that says it added
auth but never called ``app.add_middleware(AuthMiddleware)``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import w2v_common.auth.middleware as w2v_auth
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from w2v_common.auth.middleware import AuthMiddleware


@pytest.fixture(autouse=True)
def _clear_license_cache() -> None:
    cache = w2v_auth._get_license_cache()
    cache.clear()


def _make_minimal_app() -> FastAPI:
    """Tiny app for behaviour tests — independent of asgi lifespan side-effects."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/decks/echo")
    async def _echo(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "tenant_id": getattr(request.state, "tenant_id", None),
                "license_present": getattr(request.state, "license", None) is not None,
            }
        )

    return app


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_make_minimal_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Wiring check — the regression-against-false-merge assertion
# ---------------------------------------------------------------------------


def test_auth_middleware_is_wired_by_create_app() -> None:
    """The factory must register the shared AuthMiddleware when auth is enabled.

    Since adopting ``w2v_common.asgi.create_app``, auth wiring is the factory's
    responsibility (gated on ``settings.auth.enabled`` / the ``auth_enabled``
    override) rather than an explicit ``app.add_middleware`` in asgi.py. Tests
    run with ``AUTH_ENABLED`` unset, so we build a throwaway app with auth
    explicitly enabled instead of importing the auth-disabled ``src.asgi.app``,
    and assert the shared middleware landed on the stack — the same
    regression-against-false-merge guarantee.
    """
    from w2v_common.asgi import create_app
    from w2v_common.auth.middleware import AuthMiddleware as SharedAuthMiddleware

    from src.config import get_settings

    # AuthMiddleware resolves to the shared lib (not a local stub).
    assert AuthMiddleware is SharedAuthMiddleware

    app = create_app(get_settings(), auth_enabled=True)
    assert any(m.cls is SharedAuthMiddleware for m in app.user_middleware), (
        "create_app must register the shared AuthMiddleware when auth is enabled"
    )


# ---------------------------------------------------------------------------
# Public bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_is_public(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Protected paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protected_route_with_valid_license_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # w2v-common 0.67.0: the middleware parses the introspected tenant to a
    # uuid and 401s a slug ("license token has no valid tenant"), so a real
    # registered tenant uuid is the only payload that can reach the route.
    tenant = "0195f48b-d000-7000-8000-000000000001"
    introspect_payload: dict[str, Any] = {
        "active": True,
        "tenant_id": tenant,
        "subject_id": "subj-1",
        "license_id": "lic-1",
        "roles": ["user"],
    }

    async def _fake(token: str) -> dict[str, Any]:
        assert token == "abc.def.ghi"
        return introspect_payload

    monkeypatch.setattr(w2v_auth, "_introspect_license", _fake)

    transport = httpx.ASGITransport(app=_make_minimal_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/decks/echo",
            headers={"X-Tenant-Id": "Bearer abc.def.ghi"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == tenant
    assert body["license_present"] is True


@pytest.mark.asyncio
async def test_protected_route_with_invalid_license_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(_token: str) -> dict[str, Any]:
        return {"active": False, "reason": "expired"}

    monkeypatch.setattr(w2v_auth, "_introspect_license", _fake)

    transport = httpx.ASGITransport(app=_make_minimal_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/decks/echo",
            headers={"X-Tenant-Id": "Bearer dud-token"},
        )

    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "INVALID_TOKEN"
    assert body["error"]["status"] == 401
    assert "Invalid or expired license token" in body["error"]["message"]


@pytest.mark.asyncio
async def test_protected_route_no_token_untrusted_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untrusted source IP + no token must 401, regardless of TRUST_INCLUSTER_IDENTITY."""
    monkeypatch.setattr(w2v_auth, "_trust_incluster_identity", lambda: False)

    transport = httpx.ASGITransport(app=_make_minimal_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/decks/echo")
    assert resp.status_code == 401
