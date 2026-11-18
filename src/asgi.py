"""ASGI application for slide_service.

Built on the fleet-canonical ``w2v_common.asgi.create_app`` factory: CORS,
RequestId/SecurityHeaders/Auth middleware, the error envelope and the
BearerAuth/TenantIdHeader OpenAPI customization are all wired by the factory.
This module only supplies what is genuinely service-specific — the lifespan
(DB / Redis / storage / WMClient / DeckBuilder / Chromium pool), the root
banner, and the health/domain routers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from w2v_common.asgi import create_app
from w2v_common.observability import database_endpoint, redis_endpoint

from src.api.health import router as health_router
from src.api.routes import router as api_router
from src.config import get_settings
from src.logging_config import configure_logging
from src.seed.seed_templates import SystemTemplateStatus

load_dotenv()

settings = get_settings()
configure_logging(json_logs=settings.logging.json_logs, level=settings.logging.level)
logger = logging.getLogger(__name__)


# ============================================================================
# Health-check access-log filter (service-specific noise suppression)
# ============================================================================


class HealthCheckLoggingFilter(logging.Filter):
    """Suppress noisy health-check requests from uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(
            path in message
            for path in ["/health", "/health/ready", "/health/live", "/healthz", "/readyz"]
        )


logging.getLogger("uvicorn.access").addFilter(HealthCheckLoggingFilter())


# ============================================================================
# Lifespan hooks (stash on app.state so shutdown can reach what startup built)
# ============================================================================


async def _start_browser_pool() -> None:
    """Eagerly start the Chromium pool for html_slide_renderer (fail-soft)."""
    try:
        from src.services.browser_pool import init_browser_pool

        await init_browser_pool()
        logger.info("Browser pool initialized (Chromium slide rendering)")
    except Exception as e:
        logger.error("Failed to start browser pool: %s", e, exc_info=True)


async def _stop_browser_pool() -> None:
    """Stop the Chromium pool on shutdown (fail-soft)."""
    try:
        from src.services.browser_pool import close_browser_pool

        await close_browser_pool()
    except Exception as e:
        logger.error("Error stopping browser pool: %s", e, exc_info=True)


async def _seed_system_templates() -> SystemTemplateStatus:
    """Seed + verify the platform system templates; never raises.

    Returns the status the health surface reports. The verify step runs even when
    the seed reports 0 inserted, so a template added to ``SEED_TEMPLATES`` but
    never landed in the database is loud rather than invisible.
    """
    from src.database.session import get_db_session
    from src.seed.seed_templates import (
        SYSTEM_TEMPLATE_NAMES,
        missing_system_templates,
        seed_templates,
    )

    try:
        async with get_db_session() as db:
            seeded = await seed_templates(db)
            missing = await missing_system_templates(db)
    except Exception as e:
        detail = (
            f"unhealthy: system template seeding FAILED ({e!r}) -- this pod may be "
            f"serving with system templates missing; every deck built from a system "
            f"template will fail or silently degrade"
        )
        logger.error(
            "Failed to seed system templates -- decks built from system templates "
            "will fail or degrade on this pod: %s",
            e,
            exc_info=True,
        )
        return SystemTemplateStatus(ok=False, detail=detail)

    if seeded:
        logger.info("Seeded %d system templates", seeded)
    if missing:
        names = ", ".join(sorted(missing))
        detail = (
            f"unhealthy: {len(missing)} of {len(SYSTEM_TEMPLATE_NAMES)} system templates "
            f"MISSING ({names}) -- every deck requesting one of them will fail or "
            f"silently degrade"
        )
        logger.error("System templates missing after seed: %s", names)
        return SystemTemplateStatus(ok=False, detail=detail)

    return SystemTemplateStatus(
        ok=True, detail=f"healthy: all {len(SYSTEM_TEMPLATE_NAMES)} system templates present"
    )


async def on_startup(app: FastAPI) -> None:
    """Init DB, Redis, storage, WMClient, DeckBuilder, seed templates, browser pool."""
    from w2v_common.clients import WMClient

    from src.cache.redis_client import get_redis_client, init_redis
    from src.database.connection import get_engine

    logger.info("Starting Slide Service")

    # Database. Schema is owned by alembic (initContainer runs `alembic upgrade
    # head` before this app starts) — never call Base.metadata.create_all here.
    try:
        app.state.db_engine = await get_engine()
        logger.info(
            "Database connected (schema managed by alembic): %s",
            database_endpoint(settings.database.url),
        )
    except Exception as e:
        logger.error("Failed to connect to database: %s", e, exc_info=True)
        raise

    # Seed system templates.
    #
    # Fail-soft on the PROCESS (a re-raise here would CrashLoopBackOff every
    # replica of a service the whole fleet's slide stages depend on, and a
    # crashlooping pod also takes down the tenant-template CRUD, deck status
    # polling and the reaper, which are all unaffected by a missing system
    # template). But NOT fail-silent: the outcome is recorded on app.state and
    # reported by ``/health`` as ``status: unhealthy`` with the missing names,
    # which is the channel this service already reserves for "this pod cannot do
    # its job" (see src/api/health.py::health_ready for why that signal is
    # deliberately kept off the readiness probe). Swallowing this with only a
    # log line is what let both replicas serve for a day with 5 templates
    # missing.
    app.state.system_templates = await _seed_system_templates()

    # Redis (fail-open — caching disabled if unavailable).
    try:
        redis_client = await init_redis()
        await redis_client.ping()
        app.state.redis_client = get_redis_client()
        logger.info("Redis connected: %s", redis_endpoint(settings.redis.url))
    except Exception as e:
        logger.error("Failed to connect to Redis: %s", e, exc_info=True)
        logger.warning("Continuing without Redis -- caching will be disabled")

    # Services. One shared WMClient per process (connection-pooled; identity
    # headers layered per outbound request from ContextVars).
    from src.services.deck_builder import DeckBuilder
    from src.services.storage_service import StorageService

    storage_service = StorageService()
    await storage_service.initialize()
    app.state.storage_service = storage_service

    wm_client = WMClient()
    app.state.wm_client = wm_client
    app.state.deck_builder = DeckBuilder(
        settings, storage_service=storage_service, wm_client=wm_client
    )
    logger.info("Services initialized: DeckBuilder, StorageService, WMClient")

    await _start_browser_pool()

    # Stuck-deck-job reaper — multi-replica safety. A pod that dies mid-render
    # leaves its DeckJob wedged at "processing" (Redis lock TTL-expires, nothing
    # re-drives it). This lease-gated periodic loop (advisory-lock → one replica
    # acts per tick) fails such orphans. Fail-soft: a wiring error never blocks
    # startup.
    app.state.reaper_task = None
    if settings.reaper.enabled:
        try:
            from src.database.session import get_session_maker
            from src.services.deck_reaper import run_periodic_deck_reaper

            session_maker = await get_session_maker()
            app.state.reaper_task = asyncio.create_task(
                run_periodic_deck_reaper(
                    session_maker,
                    getattr(app.state, "redis_client", None),
                    stale_threshold_seconds=settings.reaper.stale_threshold_seconds,
                    interval_seconds=settings.reaper.interval_seconds,
                )
            )
        except Exception as e:
            logger.error("Failed to start stuck-job reaper: %s", e, exc_info=True)

    if settings.storage.backend == "minio" and (
        not settings.storage.minio_access_key or not settings.storage.minio_secret_key
    ):
        logger.warning(
            "STORAGE_BACKEND=minio but MinIO credentials are empty. "
            "Set STORAGE_MINIO_ACCESS_KEY and STORAGE_MINIO_SECRET_KEY "
            "or switch to STORAGE_BACKEND=local."
        )

    logger.info(
        "Slide Service started (env=%s, api=%s:%d)",
        settings.environment,
        settings.api.host,
        settings.api.port,
    )


async def on_shutdown(app: FastAPI) -> None:
    """Graceful teardown of the browser pool, Redis, DB engine, and WMClient."""
    from src.cache.redis_client import close_redis
    from src.database.connection import close_engine

    logger.info("Shutting down Slide Service")

    reaper_task = getattr(app.state, "reaper_task", None)
    if reaper_task is not None:
        reaper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper_task

    await _stop_browser_pool()
    for label, closer in (
        ("Redis", close_redis()),
        ("database", close_engine()),
    ):
        try:
            await closer
        except Exception as e:
            logger.error("Error closing %s: %s", label, e, exc_info=True)
    wm_client = getattr(app.state, "wm_client", None)
    if wm_client is not None:
        try:
            await wm_client.aclose()
        except Exception as e:
            logger.error("Error closing WMClient: %s", e, exc_info=True)
    logger.info("Slide Service shutdown complete")


# ============================================================================
# Routers
# ============================================================================

meta_router = APIRouter()


@meta_router.get(
    "/",
    operation_id="service_root",
    tags=["health"],
    summary="Service banner + endpoint index",
)
async def root() -> dict[str, Any]:
    """Return service info and available endpoint listing."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "description": "Presentation deck generation microservice",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "templates": "/api/templates",
            "decks": "/api/decks",
        },
        "status": "running",
        "environment": settings.environment,
    }


# ============================================================================
# App — canonical factory does CORS / middleware / error envelope / OpenAPI
# ============================================================================

app = create_app(
    settings,
    routers=[meta_router, health_router, api_router],
    title=settings.app_name,
    description="Presentation deck generation microservice for branded PowerPoint decks",
    version=settings.app_version,
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
    redirect_slashes=False,
    swagger_ui_parameters={"persistAuthorization": True},
    openapi_tags=[
        {"name": "health", "description": "Liveness, readiness, dependency probes."},
        {
            "name": "slides",
            "description": (
                "Slide-template CRUD/versioning and async deck generation "
                "jobs (PPTX/PDF/preview/manifest artifacts)."
            ),
        },
    ],
)


def main() -> None:
    """Main entry point for running the ASGI application."""
    import uvicorn

    uvicorn.run(
        "src.asgi:app",
        host=settings.api.host,
        port=settings.api.port,
        log_level=settings.logging.level.lower(),
        reload=settings.api.reload,
    )


if __name__ == "__main__":
    main()
