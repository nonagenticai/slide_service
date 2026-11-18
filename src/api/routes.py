"""API route handlers for slide_service domain endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid7

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from w2v_common.auth import LICENSE_GATE
from w2v_common.auth.identity_context import (
    get_correlation_id_from_context,
    get_stage_from_context,
    get_tenant_source_from_context,
    set_correlation_context,
    set_identity_context,
)
from w2v_common.tenant import TenantSource

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient

from src.api.schemas import (
    DeckCreateRequest,
    DeckJobResponse,
    DeckManifestResponse,
    DeckStatusResponse,
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdateRequest,
    TemplateVersionResponse,
)
from src.cache.cache_keys import job_status_key, job_status_ttl
from src.cache.job_lock import acquire_job_lock, release_job_lock
from src.config import get_settings
from src.database.models import DeckJob, SlideTemplate, TemplateVersion
from src.database.session import get_db, get_db_session
from src.logging_config import job_id_var
from src.services.deck_builder import DeckBuilder
from src.services.storage_service import StorageService

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()

# ENTITLEMENT GATE (w2v_common.auth.entitlement, >=0.75.0), mounted router-wide.
# Until now this service authenticated the licence token for tenant IDENTITY
# (AuthMiddleware answers "who are you") and never asked "may you" -- a tenant
# whose licence is expired or revoked still carries a syntactically valid token
# and was served. LICENSE_GATE closes that: none/expired/revoked -> 402
# license_<state>, an unestablishable verdict -> 503 license_unavailable +
# Retry-After, active/expiring_soon -> pass.
#
# EVERY route under this prefix is gated, and that is correct: /api is the
# entire tenant-scoped surface here -- template CRUD/versioning and deck
# generation/status/manifest/downloads (PPTX/PDF/preview). There is no
# internal-callback, webhook, or peer-to-peer route under this prefix, so
# nothing here has a reason to be exempt.
#
# DELIBERATELY NOT GATED, each for a stated reason:
#   * health_router (/health, /health/live, /health/ready, /healthz, /readyz),
#     mounted separately in asgi.py. A 402/503 on a probe CrashLoops the pod,
#     and an unlicensed tenant cannot be billed by killing our own deployment.
#     The gate also refuses these paths by name (is_license_exempt_path), so
#     this is belt and braces, not a single point of failure.
#   * meta_router ("/"), mounted separately in asgi.py -- a static service
#     banner, no tenant work.
#   * /metrics and /docs|/openapi.json, added by w2v_common's create_app: a
#     Prometheus scrape and the schema carry no tenant and do no tenant work.
#
# DORMANT ON MERGE: require_license is a no-op unless W2V_LICENSE_GATE_ENABLED
# is true, read per-call from os.environ. It defaults to False, so this change
# alters no behaviour until gitops arms the flag. Preserve that default.
router = APIRouter(prefix="/api", tags=["slides"], dependencies=list(LICENSE_GATE))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_deck_builder(request: Request) -> DeckBuilder:
    return request.app.state.deck_builder


def _get_storage(request: Request) -> StorageService:
    storage = getattr(request.app.state, "storage_service", None)
    if storage is not None:
        return storage
    storage = StorageService()
    request.app.state.storage_service = storage
    return storage


def _get_redis(request: Request) -> RedisClient | None:
    return getattr(request.app.state, "redis_client", None)


def _get_tenant_id(request: Request) -> str:
    """Return the caller's real tenant uuid, or 401 — never a sentinel default.

    ``request.state.tenant_id`` is set by ``w2v_common.auth.middleware.AuthMiddleware``,
    which ``w2v_common.asgi.create_app`` registers for this service (``src/asgi.py``).
    It is populated from the JWT ``tenant_id`` claim, the licenses-service
    introspection payload, or — only when ``AUTH_TRUST_INCLUSTER_IDENTITY=true`` —
    the ``X-Tenant-Id`` header on a trusted in-cluster network. Same mechanism
    ``content_service`` / ``video_production_service`` consume; nothing new invented
    here.

    This used to return the literal ``"default"``, so every caller silently shared
    one bogus tenant and ``tenant_id`` could never be a uuid. Absent identity is now
    an ERROR: ``tenant_id`` is a native ``uuid`` column, and a request with no
    tenant has nothing legitimate to read or write.

    Three rejections, all 401, all deliberate (w2v-common 0.67.0 deleted
    ``AuthSettings.default_tenant_id`` — there is no fallback tenant left to
    invent, and inventing one is the bug that release removed):

    * ABSENT / blank — no identity at all.
    * NON-UUID — a legacy slug (``n8n-dev``, ``default``) would be written into a
      native ``uuid`` column; ``uuid.UUID`` refuses it here instead of the driver
      refusing it four frames deeper.
    * The NIL uuid, rejected BY VALUE. ``00000000-0000-0000-0000-000000000000``
      was the old fail-safe substitute, it is a *valid* uuid so no type check
      catches it, and it is registered in ``licenses.tenants`` as ``default-anon``
      so it looks legitimate in an audit. ``uuid.UUID(v).int == 0`` catches every
      spelling — dashed, undashed, braced, ``urn:uuid:`` — where a string compare
      against the dashed literal catches exactly one.

    The value is returned in canonical dashed form: the ORM maps ``tenant_id``
    with ``as_uuid=False``, so the Python side holds a ``str`` and an undashed
    inbound spelling must not become a second key for the same tenant.
    """
    raw = (getattr(request.state, "tenant_id", None) or "").strip()
    if not raw:
        raise HTTPException(
            status_code=401,
            detail=(
                "No tenant identity on the request. slide_service is tenant-scoped: "
                "authenticate with a license token or JWT carrying a tenant_id claim."
            ),
        )
    try:
        parsed = UUID(raw)
    except ValueError as exc:
        # Deliberately does not echo the value: X-Tenant-Id can carry a
        # `Bearer <license token>` and logging it would leak the token.
        raise HTTPException(
            status_code=401,
            detail=(
                "Tenant identity on the request is not a uuid. slide_service stores "
                "tenant_id in a native uuid column; legacy slug tenants are not accepted."
            ),
        ) from exc
    if parsed.int == 0:
        raise HTTPException(
            status_code=401,
            detail=(
                "The nil uuid is not a tenant. It was the retired fail-safe substitute "
                "for an absent tenant and every row written under it is mis-attributed."
            ),
        )
    return str(parsed)


def _cache_enabled() -> bool:
    return get_settings().cache.enabled


def _resolve_asset_storage_path(
    manifest: dict[str, Any] | None,
    asset_name: str,
    fallback_path: str,
) -> str:
    if manifest is None:
        return fallback_path
    assets = manifest.get("assets", {})
    if isinstance(assets, dict):
        asset = assets.get(asset_name)
        if isinstance(asset, dict):
            storage_path = str(asset.get("storage_path") or "").strip()
            if storage_path:
                return storage_path
        if isinstance(asset, str):
            asset_path = asset.strip()
            if asset_path:
                return asset_path
    return fallback_path


def _not_ready_response(status: str) -> JSONResponse | None:
    """Return a 409 if the job is still processing, or raise 422 if it failed.

    Returns ``None`` when the caller should continue (i.e. status is "completed").
    The 409 body follows the org-standard error envelope shape (``error.code`` /
    ``error.message`` / ``error.status``) rather than the legacy ``detail`` key.
    """
    if status == "completed":
        return None
    if status == "failed":
        raise HTTPException(status_code=422, detail="Deck generation failed")
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "DECK_NOT_READY",
                "message": "Deck is still being generated",
                "status": 409,
                "job_status": status,
            }
        },
        headers={"Retry-After": "5"},
    )


async def _build_artifact_response(
    *,
    storage: StorageService,
    storage_path: str,
    media_type: str,
    filename: str | None = None,
) -> Response:
    if not await storage.exists(storage_path):
        raise HTTPException(status_code=404, detail="Artifact not found")

    if storage.backend_type == "local":
        return FileResponse(
            path=storage.get_full_path(storage_path),
            media_type=media_type,
            filename=filename,
        )

    headers: dict[str, str] = {}
    if filename:
        headers["content-disposition"] = f'attachment; filename="{filename}"'
    return Response(
        content=await storage.read_file(storage_path),
        media_type=media_type,
        headers=headers,
    )


@dataclass
class TaskContext:
    """Context propagated to background tasks."""

    job_id: str
    tenant_id: str
    request_id: str = ""


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------


@router.get("/templates", operation_id="list_slide_templates")
async def list_templates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    category: str | None = None,
    layout_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> TemplateListResponse:
    """List active templates with optional filtering.

    Returns templates owned by the caller's tenant plus platform templates
    (``is_system``). Other tenants' templates are hidden. Platform visibility is
    the ``is_system`` flag — the ``tenant_id = 'system'`` sentinel is retired.
    """
    tenant_id = _get_tenant_id(request)
    stmt = (
        select(SlideTemplate)
        .where(SlideTemplate.is_active.is_(True))
        .where(or_(SlideTemplate.tenant_id == tenant_id, SlideTemplate.is_system.is_(True)))
    )

    if category:
        stmt = stmt.where(SlideTemplate.category == category)
    if layout_type:
        stmt = stmt.where(SlideTemplate.layout_type == layout_type)

    # Count total
    from sqlalchemy import func

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginate
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    templates = result.scalars().all()

    return TemplateListResponse(
        templates=[
            TemplateResponse(
                id=t.template_id,
                name=t.name,
                display_name=t.display_name,
                category=t.category,
                layout_type=t.layout_type,
                slot_definitions=t.slot_definitions,
                styling_rules=t.styling_rules,
                default_transition=t.default_transition,
                default_timing_hint_seconds=t.default_timing_hint_seconds,
                is_system=t.is_system,
                is_active=t.is_active,
            )
            for t in templates
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/templates/{template_id}",
    operation_id="get_slide_template",
    responses={
        404: {"description": "Template not found"},
    },
)
async def get_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TemplateResponse:
    """Get a single template by ID.

    Visible to the caller's tenant or to all tenants (system templates).
    Returns 404 for templates owned by other tenants to avoid disclosing
    their existence.
    """
    tenant_id = _get_tenant_id(request)
    stmt = (
        select(SlideTemplate)
        .where(SlideTemplate.template_id == template_id)
        .where(or_(SlideTemplate.tenant_id == tenant_id, SlideTemplate.is_system.is_(True)))
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return TemplateResponse(
        id=template.template_id,
        name=template.name,
        display_name=template.display_name,
        category=template.category,
        layout_type=template.layout_type,
        slot_definitions=template.slot_definitions,
        styling_rules=template.styling_rules,
        default_transition=template.default_transition,
        default_timing_hint_seconds=template.default_timing_hint_seconds,
        is_system=template.is_system,
        is_active=template.is_active,
    )


@router.post(
    "/templates",
    status_code=201,
    operation_id="create_slide_template",
    responses={
        422: {"description": "Validation error"},
    },
)
async def create_template(
    body: TemplateCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TemplateResponse:
    """Create a custom template."""
    template = SlideTemplate(
        template_id=str(uuid7()),
        tenant_id=_get_tenant_id(request),
        name=body.name,
        display_name=body.display_name,
        category=body.category,
        layout_type=body.layout_type,
        slot_definitions=body.slot_definitions,
        styling_rules=body.styling_rules,
        default_transition=body.default_transition,
        default_timing_hint_seconds=body.default_timing_hint_seconds,
        is_system=False,
        is_active=True,
    )
    db.add(template)
    await db.flush()

    return TemplateResponse(
        id=template.template_id,
        name=template.name,
        display_name=template.display_name,
        category=template.category,
        layout_type=template.layout_type,
        slot_definitions=template.slot_definitions,
        styling_rules=template.styling_rules,
        default_transition=template.default_transition,
        default_timing_hint_seconds=template.default_timing_hint_seconds,
        is_system=False,
        is_active=True,
    )


@router.put(
    "/templates/{template_id}",
    operation_id="update_slide_template",
    responses={
        404: {"description": "Template not found"},
        403: {"description": "Cannot modify system templates"},
        422: {"description": "Validation error"},
    },
)
async def update_template(
    template_id: str,
    body: TemplateUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TemplateResponse:
    """Update a template. Auto-versions if the template has been used by any deck.

    Only the owning tenant can mutate a template. Templates owned by other
    tenants (or system templates) return 404 to avoid existence disclosure.
    """
    tenant_id = _get_tenant_id(request)
    stmt = (
        select(SlideTemplate)
        .where(SlideTemplate.template_id == template_id)
        .where(SlideTemplate.tenant_id == tenant_id)
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_system:
        raise HTTPException(status_code=403, detail="Cannot modify system templates")

    if body.display_name is not None:
        template.display_name = body.display_name
    if body.category is not None:
        template.category = body.category
    if body.slot_definitions is not None:
        template.slot_definitions = body.slot_definitions
    if body.styling_rules is not None:
        template.styling_rules = body.styling_rules
    if body.default_transition is not None:
        template.default_transition = body.default_transition
    if body.default_timing_hint_seconds is not None:
        template.default_timing_hint_seconds = body.default_timing_hint_seconds

    await db.flush()

    return TemplateResponse(
        id=template.template_id,
        name=template.name,
        display_name=template.display_name,
        category=template.category,
        layout_type=template.layout_type,
        slot_definitions=template.slot_definitions,
        styling_rules=template.styling_rules,
        default_transition=template.default_transition,
        default_timing_hint_seconds=template.default_timing_hint_seconds,
        is_system=template.is_system,
        is_active=template.is_active,
    )


@router.delete(
    "/templates/{template_id}",
    operation_id="delete_slide_template",
    responses={
        404: {"description": "Template not found"},
        403: {"description": "Cannot delete system templates"},
    },
)
async def delete_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Soft-delete a template (sets is_active=false).

    Only the owning tenant can delete a template. Other tenants get 404.
    """
    tenant_id = _get_tenant_id(request)
    stmt = (
        select(SlideTemplate)
        .where(SlideTemplate.template_id == template_id)
        .where(SlideTemplate.tenant_id == tenant_id)
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_system:
        raise HTTPException(status_code=403, detail="Cannot delete system templates")

    template.is_active = False
    await db.flush()
    return {"deleted": True, "id": template_id}


@router.get(
    "/templates/{template_id}/versions",
    operation_id="list_slide_template_versions",
)
async def list_template_versions(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[TemplateVersionResponse]:
    """List frozen versions of a template.

    The TemplateVersion table has no ``tenant_id`` of its own, so we first
    verify the parent ``SlideTemplate`` belongs to the caller's tenant (or
    is a system template). If not, return 404 to avoid existence disclosure.
    """
    tenant_id = _get_tenant_id(request)
    parent_stmt = (
        select(SlideTemplate.id)
        .where(SlideTemplate.template_id == template_id)
        .where(or_(SlideTemplate.tenant_id == tenant_id, SlideTemplate.is_system.is_(True)))
    )
    if (await db.execute(parent_stmt)).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Template not found")

    stmt = (
        select(TemplateVersion)
        .where(TemplateVersion.template_id == template_id)
        .order_by(TemplateVersion.version_number.desc())
    )
    result = await db.execute(stmt)
    versions = result.scalars().all()

    return [
        TemplateVersionResponse(
            version_id=v.version_id,
            template_id=v.template_id,
            version_number=v.version_number,
            snapshot_hash=v.snapshot_hash,
            slot_definitions=v.slot_definitions,
            styling_rules=v.styling_rules,
            default_transition=v.default_transition,
            default_timing_hint_seconds=v.default_timing_hint_seconds,
            created_at=v.created_at.isoformat() if v.created_at else None,
        )
        for v in versions
    ]


# ---------------------------------------------------------------------------
# Deck Generation
# ---------------------------------------------------------------------------


@router.post(
    "/decks",
    status_code=202,
    operation_id="start_deck_generation",
    responses={
        422: {"description": "Validation error"},
    },
)
async def create_deck(
    body: DeckCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DeckJobResponse:
    """Start deck generation job (returns 202)."""
    logger.info(
        "Received deck request: title=%s, slides=%d",
        body.title,
        len(body.slides),
    )
    job_id = str(uuid7())

    # Create processing job in DB
    job = DeckJob(
        job_id=job_id,
        tenant_id=_get_tenant_id(request),
        status="processing",
        request_payload=body.model_dump(mode="json"),
    )
    db.add(job)
    await db.flush()
    await db.commit()

    builder = _get_deck_builder(request)
    redis = _get_redis(request)

    task = asyncio.create_task(
        _process_deck(
            builder,
            job_id,
            body.model_dump(mode="json"),
            request.app,
            redis,
            _get_tenant_id(request),
            # Snapshot cost-correlation now (request scope, where AuthMiddleware
            # populated it). The detached task below doesn't inherit ContextVars.
            get_correlation_id_from_context(),
            get_stage_from_context(),
            # Snapshot the tenant PROVENANCE too (w2v-common 0.67.0). The
            # outbound hook may only delegate a tenant to a metered peer -- WM,
            # which this job's LLM shaping calls -- when the source is vouched,
            # and `tenant_source=None` is treated as unvouched, NOT as neutral.
            # Re-establishing identity in the task without it would silently
            # drop X-Tenant-Id on every WM call and mis-attribute the cost.
            get_tenant_source_from_context(),
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info("Deck job %s accepted for processing", job_id)
    return DeckJobResponse(job_id=job_id, status="processing")


async def _process_deck(
    builder: DeckBuilder,
    job_id: str,
    request_payload: dict[str, Any],
    app: object,
    redis: RedisClient | None,
    tenant_id: str,
    correlation_id: str = "",
    stage: str = "",
    tenant_source: TenantSource | None = None,
) -> None:
    """Background task: generate deck and persist results."""
    job_id_var.set(job_id)
    # Seed the identity ContextVar so every get_db_session() in this task binds
    # app.current_tenant for RLS (the bg task runs outside the request scope, and
    # each DB phase opens its own short session). `tenant_source` carries the
    # request's provenance across the task boundary so a vouched tenant stays
    # delegable to WM; see the snapshot comment at the call site.
    set_identity_context(tenant_id, None, None, tenant_source=tenant_source)
    # Re-establish cost-correlation (run_id + DAG stage) so this task's WM LLM
    # call propagates X-Correlation-Id/X-Stage → WM stamps them on the cost_event.
    set_correlation_context(correlation_id, stage)

    if not await acquire_job_lock(redis, job_id):
        logger.info("Deck job %s: skipped (locked by another pod)", job_id)
        return

    # Heartbeat: periodically bump this job's updated_at so the stuck-job reaper
    # (src.services.deck_reaper) never fails a genuinely-running long job. The
    # Redis lock (300s TTL) can lapse mid-render, so the reaper's lock-absence
    # check alone would not protect a >5-min job — a fresh updated_at does.
    heartbeat = asyncio.create_task(
        _heartbeat_deck_job(job_id, get_settings().reaper.heartbeat_seconds)
    )

    t0 = time.monotonic()
    try:
        try:
            logger.info("Deck job %s: starting generation", job_id)

            # DB phase: resolve templates + freeze versions in a short session that
            # commits and closes, so no connection is held idle-in-transaction across
            # the long generation below (LLM shaping + PPTX/PDF render + storage I/O).
            async with get_db_session() as db:
                template_map, version_map = await builder.prepare_templates(
                    request_payload["slides"], db
                )

            # Generation phase: holds NO DB connection across the long awaits.
            manifest = await builder.build_deck(job_id, request_payload, template_map, version_map)
        except Exception as exc:
            # Catch broadly: build_deck spans LLM (httpx), Chromium render
            # (playwright), LibreOffice PDF and storage I/O, which raise many
            # exception types beyond OSError/RuntimeError (e.g. ValueError,
            # httpx.HTTPError). A narrower clause would let the fire-and-forget
            # task die with the job row stuck non-terminal at "processing".
            # NOTE: only the BUILD is guarded here — the completion write below is
            # deliberately OUTSIDE this try so a transient error committing
            # "completed" cannot downgrade a deck whose pptx/pdf/manifest are
            # already written to storage and downloadable to "failed".
            logger.error("Deck job %s failed: %s", job_id, exc, exc_info=True)
            try:
                async with get_db_session() as db:
                    stmt = select(DeckJob).where(DeckJob.job_id == job_id)
                    row = (await db.execute(stmt)).scalar_one_or_none()
                    if row:
                        row.status = "failed"
                        row.error = str(exc)
            except Exception:
                logger.exception("Failed to mark deck job %s as failed", job_id)
            return

        # Build succeeded: artifacts are in storage. The completion write is OUTSIDE
        # the build try — a transient failure here leaves the job non-terminal
        # ("processing", recoverable by the reaper / a re-poll) instead of falsely
        # marking a downloadable deck "failed".
        elapsed = time.monotonic() - t0
        logger.info("Deck job %s: completed in %.2fs", job_id, elapsed)

        completion_persisted = False
        try:
            async with get_db_session() as db:
                stmt = select(DeckJob).where(DeckJob.job_id == job_id)
                row = (await db.execute(stmt)).scalar_one_or_none()
                row_found = row is not None
                if row:
                    row.status = "completed"
                    row.manifest = manifest
                    row.pptx_storage_path = f"decks/{job_id}/deck.pptx"
                    row.slide_count = manifest.get("slide_count", 0)
            # Set only AFTER the context manager has exited, because that exit is
            # where the COMMIT happens — and the commit is the transient this whole
            # block guards against. Setting it beside the attribute assignments
            # above would mark the completion durable while it was still uncommitted,
            # which is the original bug wearing a flag.
            completion_persisted = row_found
        except Exception:
            logger.exception(
                "Deck job %s built + stored but the completion write failed; "
                "leaving status non-terminal (artifacts are downloadable)",
                job_id,
            )

        # Write to Redis cache (fail-open).
        #
        # ⚠️ GATED ON THE DB WRITE HAVING ACTUALLY LANDED. This block used to run
        # unconditionally, including down the `except` path above — so a transient
        # commit failure left the row non-terminal (by design, "recoverable by the
        # reaper / a re-poll") while Redis was seeded with a terminal
        # ``"completed"`` for the full 300s ``job_status_ttl``. ``get_deck_status``
        # PREFERS the cache over the row, but every artifact route reads
        # ``row.status`` through ``_not_ready_response`` — so the poll answered
        # ``completed`` (terminal: the orchestrator stops polling) while
        # ``GET /decks/{id}/pptx`` answered 409, and once the reaper failed the
        # stalled row, 422. The cache did not merely hide the gap, it AMPLIFIED a
        # one-off commit blip into five minutes of a confidently wrong terminal
        # answer, and it defeated the recovery the non-terminal row exists to allow.
        #
        # The same reasoning covers ``row is None`` (job row gone): nothing recorded
        # the completion, so nothing may publish it.
        if completion_persisted:
            try:
                if redis and _cache_enabled():
                    await redis.set(
                        job_status_key(job_id),
                        {
                            "status": "completed",
                            "slide_count": manifest.get("slide_count", 0),
                        },
                        ttl=job_status_ttl("completed"),
                    )
            except (OSError, RuntimeError) as exc:
                logger.debug("Redis cache write failed: %s", exc)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await release_job_lock(redis, job_id)


async def _heartbeat_deck_job(job_id: str, interval_seconds: int) -> None:
    """Bump ``DeckJob.updated_at`` every ``interval_seconds`` while the job runs.

    The reaper treats a stale ``updated_at`` (older than its threshold) as one of two
    signals that a job is orphaned. A live job refreshing its ``updated_at`` here can
    never satisfy that condition, so it is never reaped no matter how long it renders.
    Best-effort: a transient DB error is logged and the loop keeps beating.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with get_db_session() as db:
                await db.execute(
                    update(DeckJob)
                    .where(DeckJob.job_id == job_id, DeckJob.status == "processing")
                    .values(updated_at=func.now())
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- heartbeat must survive ANY transient
            # failure and keep beating: if it died, a >15-min live job (whose 300s Redis
            # lock has long lapsed) would look stale to the reaper and be wrongly failed.
            logger.debug("Heartbeat for deck job %s failed: %s", job_id, exc)


@router.get(
    "/decks/{job_id}",
    operation_id="get_deck_status",
    responses={
        404: {"description": "Job not found"},
    },
)
async def get_deck_status(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DeckStatusResponse:
    """Poll deck job status."""
    redis = _get_redis(request)

    # Always verify tenant ownership against the DB before trusting any
    # cache entry -- the Redis key is tenant-agnostic, so a cache hit alone
    # would otherwise leak status across tenants.
    tenant_id = _get_tenant_id(request)
    stmt = select(DeckJob).where(DeckJob.job_id == job_id).where(DeckJob.tenant_id == tenant_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    # Owning tenant confirmed -- prefer fresher status data from Redis
    # if available (fail-open).
    #
    # ``_cache_enabled()`` is checked HERE as well as on the write path. It used to
    # guard only the writer in ``_process_deck``, so setting ``CACHE_ENABLED=false``
    # -- the operator's only lever for taking a misbehaving cache out of the answer
    # path during an incident -- left this poll still SERVING from Redis and still
    # REPOPULATING it on every request. The kill-switch silently did nothing for the
    # one endpoint that prefers cache over the database row.
    try:
        if redis and _cache_enabled():
            cached = await redis.get(job_status_key(job_id))
            if cached and isinstance(cached, dict):
                return DeckStatusResponse(
                    job_id=job_id,
                    status=cached["status"],
                    slide_count=cached.get("slide_count"),
                    error=cached.get("error"),
                )
    except (OSError, RuntimeError) as exc:
        logger.debug("Redis cache read failed: %s", exc)

    result = DeckStatusResponse(
        job_id=job_id,
        status=row.status,
        slide_count=row.slide_count,
        created_at=row.created_at.isoformat() if row.created_at else None,
        error=row.error,
    )

    # Write back to Redis (fail-open); same kill-switch as the read above.
    try:
        if redis and _cache_enabled():
            await redis.set(
                job_status_key(job_id),
                {
                    "status": row.status,
                    "slide_count": row.slide_count,
                    "error": row.error,
                },
                ttl=job_status_ttl(row.status),
            )
    except (OSError, RuntimeError) as exc:
        logger.debug("Redis cache writeback failed: %s", exc)

    return result


@router.get(
    "/decks/{job_id}/pptx",
    operation_id="download_deck_pptx",
    responses={
        404: {"description": "Job or PPTX file not found"},
        409: {"description": "Job still processing"},
        422: {"description": "Deck generation failed"},
    },
)
async def download_pptx(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download generated .pptx file."""
    tenant_id = _get_tenant_id(request)
    stmt = select(DeckJob).where(DeckJob.job_id == job_id).where(DeckJob.tenant_id == tenant_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if resp := _not_ready_response(row.status):
        return resp

    manifest: dict[str, Any] = cast(dict[str, Any], row.manifest) if row.manifest else {}
    storage_path = row.pptx_storage_path or _resolve_asset_storage_path(
        manifest, "pptx", f"decks/{job_id}/deck.pptx"
    )
    if not storage_path:
        raise HTTPException(status_code=404, detail="PPTX file not found")

    return await _build_artifact_response(
        storage=_get_storage(request),
        storage_path=storage_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{job_id}.pptx",
    )


@router.get(
    "/decks/{job_id}/pdf",
    operation_id="download_deck_pdf",
    responses={
        404: {"description": "Job or PDF file not found"},
        409: {"description": "Job still processing"},
        422: {"description": "Deck generation failed"},
    },
)
async def download_pdf(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download generated PDF file."""
    tenant_id = _get_tenant_id(request)
    stmt = select(DeckJob).where(DeckJob.job_id == job_id).where(DeckJob.tenant_id == tenant_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if resp := _not_ready_response(row.status):
        return resp

    manifest = cast(dict[str, Any], row.manifest) if row.manifest else {}
    storage_path = _resolve_asset_storage_path(manifest, "pdf", f"decks/{job_id}/deck.pdf")

    if not storage_path:
        raise HTTPException(status_code=404, detail="PDF file not found")
    return await _build_artifact_response(
        storage=_get_storage(request),
        storage_path=storage_path,
        media_type="application/pdf",
        filename=f"{job_id}.pdf",
    )


@router.get(
    "/decks/{job_id}/slides/{slide_index}.png",
    operation_id="download_deck_slide_preview",
    responses={
        404: {"description": "Job or slide preview not found"},
        409: {"description": "Job still processing"},
        422: {"description": "Deck generation failed"},
    },
)
async def download_slide_preview(
    job_id: str,
    slide_index: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download a generated slide preview PNG."""
    tenant_id = _get_tenant_id(request)
    stmt = select(DeckJob).where(DeckJob.job_id == job_id).where(DeckJob.tenant_id == tenant_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if resp := _not_ready_response(row.status):
        return resp

    manifest_data: dict[str, Any] = cast(dict[str, Any], row.manifest) if row.manifest else {}
    assets_data: dict[str, Any] = manifest_data.get("assets", {})
    slide_assets_list: list[dict[str, Any]] = assets_data.get("slides", [])
    storage_path = ""
    for asset in slide_assets_list if isinstance(slide_assets_list, list) else []:
        if isinstance(asset, dict) and int(asset.get("index", -1)) == slide_index:
            storage_path = str(asset.get("storage_path") or "").strip()
            break
    if not storage_path:
        storage_path = f"decks/{job_id}/slides/slide_{slide_index + 1:02d}.png"

    storage = _get_storage(request)
    if not await storage.exists(storage_path):
        raise HTTPException(status_code=404, detail="Slide preview not found")
    return await _build_artifact_response(
        storage=storage,
        storage_path=storage_path,
        media_type="image/png",
    )


@router.get(
    "/decks/{job_id}/manifest",
    response_model=DeckManifestResponse,
    operation_id="get_deck_manifest",
    responses={
        404: {"description": "Job or manifest not found"},
        409: {"description": "Job still processing"},
        422: {"description": "Deck generation failed"},
    },
)
async def get_manifest(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response | DeckManifestResponse:
    """Download manifest JSON."""
    tenant_id = _get_tenant_id(request)
    stmt = select(DeckJob).where(DeckJob.job_id == job_id).where(DeckJob.tenant_id == tenant_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if resp := _not_ready_response(row.status):
        return resp
    if not row.manifest:
        raise HTTPException(status_code=404, detail="Manifest not found")

    manifest_json: dict[str, Any] = cast(dict[str, Any], row.manifest)
    return DeckManifestResponse(**manifest_json)
