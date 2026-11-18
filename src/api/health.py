"""Health check endpoints for slide_service."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.cache.redis_client import get_redis_client
from src.config import get_settings
from src.database.connection import get_engine
from src.services.browser_pool import pool_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _browser_pool_component() -> tuple[str, bool]:
    """Report the Chromium pool -- the component that actually renders a slide.

    DB and Redis being up says nothing about it: ``_start_browser_pool`` swallows
    its own failure (fail-soft, so a driver hiccup never blocks startup), which
    means "pool absent" is a live production state, not a theoretical one.

    ``pool_status`` reads the module global directly rather than going through
    the start-on-demand accessor, so this reports the pool the render path would
    find instead of one the probe just conjured into existence.
    """
    status = pool_status()
    if not status.started or status.capacity == 0:
        return (
            "unhealthy: browser pool not started -- this pod cannot render a slide",
            False,
        )
    return (
        f"healthy: {status.capacity} slot(s), {status.idle} idle, {status.live} browser(s) live",
        True,
    )


def _deck_reaper_component(request: Request) -> tuple[str, bool]:
    """Report the stuck-deck-job reaper loop.

    A dead reaper is invisible by construction: nothing polls it and nothing
    fails, decks simply stay wedged at ``processing`` forever.
    """
    if not get_settings().reaper.enabled:
        return "not configured", True
    task = getattr(request.app.state, "reaper_task", None)
    if task is None:
        return "unhealthy: reaper enabled but never started -- wedged decks are never failed", False
    if task.done():
        return "unhealthy: reaper loop has exited -- wedged decks are never failed", False
    return "healthy", True


def _system_templates_component(request: Request) -> tuple[str, bool]:
    """Report the platform system-template seed.

    The seed runs once at startup and is fail-soft on the process (a re-raise
    would crashloop every replica). That fail-soft used to be fail-SILENT: on
    2026-08-19 both prod replicas started with the seed aborting under RLS, went
    Ready, and served for a day with 5 of 16 system templates missing -- every
    slide stage in the fleet silently degraded, with nothing but one ERROR line
    scrolled off the top of the log to say so. This component is that missing
    signal: it names the missing templates and the consequence, and drives
    ``/health`` to ``unhealthy``.

    Absent state (no startup hook ran, e.g. a router-only test app) is reported
    as unknown-but-OK rather than manufacturing a fault.
    """
    status = getattr(request.app.state, "system_templates", None)
    if status is None:
        return "not checked", True
    return status.detail, bool(status.ok)


@router.get("/health", response_model=None, operation_id="health_check")
async def health_check(request: Request) -> dict[str, Any] | JSONResponse:
    """Combined health check for all components.

    Reports the three components that actually do the work — the Chromium pool
    that renders slides, the system-template seed that supplies what it renders,
    and the reaper loop that unwedges dead deck jobs — alongside the DB/Redis
    dependencies. None is wired into readiness; see :func:`health_ready` for why.
    """
    try:
        db_status = "healthy"
        try:
            engine = await get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (OSError, RuntimeError, SQLAlchemyError) as e:
            # ``SQLAlchemyError`` is the one that actually fires in production: a
            # dead Postgres surfaces as ``OperationalError``, which descends from
            # ``SQLAlchemyError`` and from neither ``OSError`` nor ``RuntimeError``.
            # Without it the driver exception escapes and the probe 500s instead
            # of reporting the component as unhealthy.
            db_status = f"unhealthy: {e}"

        redis_status = "healthy"
        try:
            redis_client = get_redis_client()
            if redis_client:
                healthy = await redis_client.ping()
                if not healthy:
                    redis_status = "unhealthy: ping failed"
            else:
                redis_status = "not configured"
        except (OSError, RuntimeError, SQLAlchemyError) as e:
            redis_status = f"unhealthy: {e}"

        browser_status, browser_ok = _browser_pool_component()
        reaper_status, reaper_ok = _deck_reaper_component(request)
        templates_status, templates_ok = _system_templates_component(request)

        deps_ok = db_status == "healthy" and redis_status == "healthy"
        if not browser_ok or not templates_ok:
            # No renderer at all, or no system templates to render: this pod
            # produces nothing usable. Distinguished from "degraded" so an alert
            # can tell "a dependency is flaky" from "this pod 500s every deck it
            # is handed".
            overall = "unhealthy"
        elif not deps_ok or not reaper_ok:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "status": overall,
            "components": {
                "database": db_status,
                "redis": redis_status,
                "api": "healthy",
                "browser_pool": browser_status,
                "deck_reaper": reaper_status,
                "system_templates": templates_status,
            },
        }
    except (OSError, RuntimeError, SQLAlchemyError) as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )


@router.get("/health/live", response_model=None, operation_id="health_live")
async def health_live() -> dict[str, str]:
    """Kubernetes liveness probe."""
    return {"status": "ok"}


@router.get("/health/ready", response_model=None, operation_id="health_ready")
async def health_ready() -> dict[str, Any] | JSONResponse:
    """Kubernetes readiness probe -- checks database connectivity.

    The browser pool is deliberately NOT checked here, and that is a decision,
    not an omission. Unlike the queue-driven siblings, slide rendering IS driven
    by the HTTP request — so gating readiness on the pool looks right, and is a
    trap:

    * **The pool self-heals on the next render.** ``get_browser_pool`` is
      start-on-demand, so a pod whose eager startup launch failed transiently
      recovers the first time a render arrives. Failing readiness removes exactly
      the traffic that would trigger that recovery, converting a transient
      startup failure into a permanent one — the pod can never become ready
      again, because nothing will ever ask it to render.
    * **A non-transient cause is shared.** A bad image or a node without the
      Chromium deps takes out every replica identically, so readiness would evict
      the whole Deployment at once and remove the template/CRUD/status routes as
      well, on top of a render outage.

    The system-template seed is kept off readiness for the second reason above,
    which applies to it exactly: a failed seed is a SHARED cause (same DB, same
    policies), so gating readiness on it 503s every replica at once and takes out
    tenant-owned templates, deck CRUD, job-status polling and the reaper — none
    of which need a system template — to signal a fault that degrades only the
    subset of decks that request one. It lands on ``/health`` as ``unhealthy``
    instead: loud to an operator and to alerting, inert to the scheduler.

    So the signal lands on ``/health`` as ``status: unhealthy`` — loud to an
    operator and to alerting, inert to the scheduler. The reaper is only ever
    ``degraded``: it is advisory-lock gated so exactly one replica acts per tick,
    and a sibling holding that lock keeps decks unwedged regardless of this pod.
    """
    try:
        engine = await get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "ready": True, "checks": {"database": {"status": "up"}}}
    except (OSError, RuntimeError, SQLAlchemyError) as e:
        # The whole point of this probe is the dead-DB case, and that case raises
        # ``sqlalchemy.exc.OperationalError`` -- a ``SQLAlchemyError``, not an
        # ``OSError``. Catching only the latter meant the real outage escaped the
        # handler and K8s saw a 500, which is "the app is broken", not "not ready
        # yet, keep me in the Deployment and stop sending me traffic".
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "ready": False,
                "checks": {"database": {"status": "down", "error": str(e)}},
            },
        )


@router.get("/healthz", response_model=None, operation_id="health.live")
async def healthz() -> dict[str, str]:
    """Modern K8s liveness probe path (alias of /health/live)."""
    return await health_live()


@router.get("/readyz", response_model=None, operation_id="health.ready")
async def readyz() -> dict[str, Any] | JSONResponse:
    """Modern K8s readiness probe path (alias of /health/ready)."""
    return await health_ready()
