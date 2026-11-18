"""Seeds the 16 platform-owned system templates on startup (RLS-aware).

The rows are written with ``is_system = True`` and a REAL registered tenant uuid
(:data:`SYSTEM_TEMPLATE_TENANT_ID`). Sentinels are banned fleet-wide: the retired
``tenant_id = 'system'`` string cannot even be stored now that the column is a
native ``uuid`` (0005_tenant_id_uuid). The ``slide_templates`` RLS policy set
exposes ``is_system`` rows to every tenant for READ (``tenant_isolation_read``)
but restricts WRITE to the tenant bound in the
``app.current_tenant`` GUC (``tenant_isolation_ins``, see
``src/database/rls_policies.py``). At startup there is no request identity, so
the GUC is unbound → NULL → the INSERT ``WITH CHECK`` fails and the whole seed
transaction aborts with ``InsufficientPrivilege``. :func:`seed_templates`
therefore binds :data:`SYSTEM_TEMPLATE_TENANT_ID` explicitly for its own
transaction, which is the
documented contract for background/no-request paths in
``w2v_common.database.session`` ("such tasks must call ``bind_session_tenant``
with the known tenant"). No SECURITY DEFINER, no owner role, no BYPASSRLS.

The same reasoning extends to ``template_versions``: the request path may READ a
platform template but may never freeze a version of one, because
``tenant_isolation_ins`` requires the parent template be owned by the calling
tenant. :func:`freeze_system_template_versions` therefore does that freeze here,
in the seed's owner-bound transaction — see its docstring for the outage.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID, uuid7

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from w2v_common.database.rls import bind_session_tenant

from src.database.models import SlideTemplate, TemplateVersion
from src.services.template_snapshot import snapshot_hash_for

logger = logging.getLogger(__name__)

#: Real registered tenant that OWNS every platform template row. Verified present
#: in ``licenses.tenants`` as ``fr-test-user``. This is an owner, NOT a sentinel:
#: cross-tenant visibility comes from the ``is_system`` flag, never from this value.
SYSTEM_TEMPLATE_TENANT_ID = "0195f48b-d000-7000-8000-000000000099"

#: Postgres advisory-lock key for the seed transaction. The Deployment runs 2+
#: replicas that start together and there is NO unique constraint on
#: ``(name, is_system)`` — without this, two replicas both read "absent" and both
#: INSERT, duplicating all 16 templates.
_SEED_ADVISORY_LOCK_KEY = 0x51DE_5EED  # "slide seed"


@dataclass(frozen=True, slots=True)
class SystemTemplateStatus:
    """Outcome of the startup seed/verify, surfaced on ``/health``."""

    ok: bool
    detail: str


SEED_TEMPLATES = [
    {
        "name": "title",
        "display_name": "Title Slide",
        "category": "narrative",
        "layout_type": "title",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "subtitle": {"type": "text", "max_length": 120, "required": False},
            # NO ``logo`` slot. It used to be declared here as an ``image_url``,
            # but nothing draws it: the HTML ``title`` family renders only
            # eyebrow/title/subtitle, and ``templates.title.TitleRenderer`` never
            # reads ``logo``. A declared-but-undrawn slot is a promise the product
            # cannot keep — and it was worse than cosmetic, because the ref was
            # counted as a FAILED image load and the all-images-lost guard failed
            # the whole job. Removing the declaration is the honest fix and the
            # minimal one: no tenant can be relying on rendered output from a slot
            # that has never rendered. Drawing a logo is a design change (position,
            # sizing, safe area, and the same treatment in the PPTX renderer), not
            # a correctness fix, so it is deliberately left out of scope here.
        },
        "styling_rules": {
            "title_font_size": 40,
            "subtitle_font_size": 24,
            "layout": "centered",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 8.0,
    },
    {
        "name": "agenda",
        "display_name": "Agenda",
        "category": "narrative",
        "layout_type": "agenda",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "items": {
                "type": "list",
                "max_items": 10,
                "required": True,
                "item_schema": {"text": {"type": "text", "max_length": 100}},
            },
        },
        "styling_rules": {
            "title_font_size": 28,
            "item_font_size": 18,
            "layout": "bullet_list",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 10.0,
    },
    {
        "name": "scorecard",
        "display_name": "Scorecard",
        "category": "data",
        "layout_type": "scorecard",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "metrics": {
                "type": "list",
                "max_items": 6,
                "required": True,
                "item_schema": {
                    "label": {"type": "text", "max_length": 40},
                    "value": {"type": "text", "max_length": 20},
                },
            },
            "footer": {"type": "text", "max_length": 120, "required": False},
        },
        "styling_rules": {
            "background": "brand_primary_light",
            "title_font_size": 28,
            "metric_font_size": 36,
            "layout": "grid",
            "grid_columns": 3,
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 12.0,
    },
    {
        "name": "finding_detail",
        "display_name": "Finding Detail",
        "category": "data",
        "layout_type": "finding_detail",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "description": {"type": "text", "max_length": 500, "required": True},
            "severity": {"type": "text", "max_length": 20, "required": False},
            "recommendation": {"type": "text", "max_length": 300, "required": False},
            "screenshot_url": {"type": "image_url", "required": False},
            "image_placement": {
                "type": "text",
                "required": False,
                "default": "image_frame",
                "enum": ["image_frame", "full_bleed"],
            },
        },
        "styling_rules": {
            "title_font_size": 24,
            "description_font_size": 14,
            "layout": "two_column",
            "image_placement": "image_frame",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 15.0,
    },
    {
        "name": "comparison",
        "display_name": "Comparison",
        "category": "narrative",
        "layout_type": "comparison",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "before_label": {"type": "text", "max_length": 40, "required": True},
            "before_image_url": {"type": "image_url", "required": False},
            "after_label": {"type": "text", "max_length": 40, "required": True},
            "after_image_url": {"type": "image_url", "required": False},
            "caption": {"type": "text", "max_length": 200, "required": False},
            "image_placement": {
                "type": "text",
                "required": False,
                "default": "image_frame",
                "enum": ["image_frame", "full_bleed"],
            },
        },
        "styling_rules": {
            "title_font_size": 28,
            "label_font_size": 20,
            "layout": "side_by_side",
            "image_placement": "image_frame",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 12.0,
    },
    {
        "name": "feature_spotlight",
        "display_name": "Feature Spotlight",
        "category": "narrative",
        "layout_type": "feature_spotlight",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "description": {"type": "text", "max_length": 300, "required": True},
            "image_url": {"type": "image_url", "required": False},
            "bullets": {
                "type": "list",
                "max_items": 5,
                "required": False,
                "item_schema": {"text": {"type": "text", "max_length": 100}},
            },
            "image_placement": {
                "type": "text",
                "required": False,
                "default": "image_frame",
                "enum": ["image_frame", "full_bleed"],
            },
        },
        "styling_rules": {
            "title_font_size": 28,
            "description_font_size": 16,
            "layout": "hero_text",
            "image_placement": "image_frame",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 12.0,
    },
    {
        "name": "process_flow",
        "display_name": "Process Flow",
        "category": "narrative",
        "layout_type": "process_flow",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "steps": {
                "type": "list",
                "max_items": 8,
                "required": True,
                "item_schema": {
                    "label": {"type": "text", "max_length": 60},
                    "description": {"type": "text", "max_length": 120},
                },
            },
        },
        "styling_rules": {
            "title_font_size": 28,
            "step_font_size": 14,
            "layout": "numbered_steps",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 15.0,
    },
    {
        "name": "metric_summary",
        "display_name": "Metric Summary",
        "category": "data",
        "layout_type": "metric_summary",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "kpis": {
                "type": "list",
                "max_items": 6,
                "required": True,
                "item_schema": {
                    "label": {"type": "text", "max_length": 40},
                    "value": {"type": "text", "max_length": 20},
                    "trend": {"type": "text", "max_length": 20},
                },
            },
            "trend_description": {"type": "text", "max_length": 200, "required": False},
        },
        "styling_rules": {
            "title_font_size": 28,
            "kpi_font_size": 32,
            "layout": "kpi_dashboard",
            "grid_columns": 3,
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 12.0,
    },
    {
        "name": "roadmap",
        "display_name": "Roadmap",
        "category": "narrative",
        "layout_type": "roadmap",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "phases": {
                "type": "list",
                "max_items": 6,
                "required": True,
                "item_schema": {
                    "name": {"type": "text", "max_length": 40},
                    "description": {"type": "text", "max_length": 120},
                    "timeline": {"type": "text", "max_length": 30},
                },
            },
        },
        "styling_rules": {
            "title_font_size": 28,
            "phase_font_size": 14,
            "layout": "timeline",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 15.0,
    },
    {
        "name": "cta",
        "display_name": "Call to Action",
        "category": "cta",
        "layout_type": "cta",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "body": {"type": "text", "max_length": 300, "required": True},
            "cta_text": {"type": "text", "max_length": 40, "required": True},
            "cta_url": {"type": "text", "max_length": 200, "required": False},
            "contact_info": {"type": "text", "max_length": 200, "required": False},
        },
        "styling_rules": {
            "title_font_size": 32,
            "body_font_size": 18,
            "cta_font_size": 24,
            "layout": "cta_card",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 10.0,
    },
    {
        "name": "tutorial_step",
        "display_name": "Tutorial Step",
        "category": "narrative",
        "layout_type": "tutorial_step",
        "slot_definitions": {
            "step_number": {"type": "text", "max_length": 10, "required": True},
            "title": {"type": "text", "max_length": 80, "required": True},
            "instruction": {"type": "text", "max_length": 500, "required": True},
            "screenshot_url": {"type": "image_url", "required": False},
            "tip": {"type": "text", "max_length": 200, "required": False},
            "image_placement": {
                "type": "text",
                "required": False,
                "default": "image_frame",
                "enum": ["image_frame", "full_bleed"],
            },
        },
        "styling_rules": {
            "title_font_size": 24,
            "instruction_font_size": 16,
            "tip_font_size": 14,
            "layout": "step_by_step",
            "image_placement": "image_frame",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 15.0,
    },
    {
        "name": "quote",
        "display_name": "Pull Quote",
        "category": "narrative",
        "layout_type": "quote",
        "slot_definitions": {
            "quote": {"type": "text", "max_length": 320, "required": True},
            "attribution": {"type": "text", "max_length": 80, "required": False},
            "role": {"type": "text", "max_length": 80, "required": False},
        },
        "styling_rules": {
            "quote_font_size": 44,
            "attribution_font_size": 18,
            "layout": "centered_quote",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 8.0,
    },
    {
        "name": "stat",
        "display_name": "Stat Hero",
        "category": "data",
        "layout_type": "stat",
        "slot_definitions": {
            "value": {"type": "text", "max_length": 20, "required": True},
            "label": {"type": "text", "max_length": 80, "required": True},
            "caption": {"type": "text", "max_length": 160, "required": False},
        },
        "styling_rules": {
            "value_font_size": 160,
            "label_font_size": 32,
            "layout": "hero_number",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 6.0,
    },
    {
        "name": "section_divider",
        "display_name": "Section Divider",
        "category": "narrative",
        "layout_type": "section_divider",
        "slot_definitions": {
            "eyebrow": {"type": "text", "max_length": 40, "required": False},
            "title": {"type": "text", "max_length": 80, "required": True},
            "items": {
                "type": "list",
                "max_items": 8,
                "required": False,
                "item_schema": {"text": {"type": "text", "max_length": 100}},
            },
        },
        "styling_rules": {
            "title_font_size": 56,
            "eyebrow_font_size": 20,
            "layout": "chapter_break",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 6.0,
    },
    {
        "name": "timeline",
        "display_name": "Timeline",
        "category": "narrative",
        "layout_type": "timeline",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": True},
            "beats": {
                "type": "list",
                "max_items": 6,
                "required": True,
                "item_schema": {
                    "marker": {"type": "text", "max_length": 12},
                    "label": {"type": "text", "max_length": 40},
                    "description": {"type": "text", "max_length": 120},
                },
            },
        },
        "styling_rules": {
            "title_font_size": 32,
            "label_font_size": 20,
            "layout": "horizontal_timeline",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 15.0,
    },
    {
        "name": "two_up",
        "display_name": "Two Up",
        "category": "narrative",
        "layout_type": "two_up",
        "slot_definitions": {
            "title": {"type": "text", "max_length": 80, "required": False},
            "left": {
                "type": "object",
                "required": False,
                "item_schema": {
                    "heading": {"type": "text", "max_length": 60},
                    "body": {"type": "text", "max_length": 300},
                    "bullets": {"type": "list", "max_items": 6},
                },
            },
            "right": {
                "type": "object",
                "required": False,
                "item_schema": {
                    "heading": {"type": "text", "max_length": 60},
                    "body": {"type": "text", "max_length": 300},
                    "bullets": {"type": "list", "max_items": 6},
                },
            },
        },
        "styling_rules": {
            "title_font_size": 32,
            "heading_font_size": 22,
            "layout": "two_column",
        },
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 12.0,
    },
]


#: Names every system template that MUST exist for the fleet's slide stages.
SYSTEM_TEMPLATE_NAMES: frozenset[str] = frozenset(str(t["name"]) for t in SEED_TEMPLATES)


async def _prepare_seed_transaction(db: AsyncSession, tenant_id: str) -> None:
    """Make the current transaction able to write ``tenant_id``-owned templates.

    Two statements, both scoped to this transaction:

    * ``bind_session_tenant`` sets the ``app.current_tenant`` GUC so the
      ``tenant_isolation_ins`` ``WITH CHECK`` predicate matches. ``mode="enforce"``
      is passed explicitly rather than read from ``TENANT_RLS_MODE`` so the seed
      binds identically in both modes — under ``off`` the policies are inert and
      setting the GUC is a harmless no-op, and we never want the write path to
      behave one way in tests/dev and another in prod.
    * ``pg_advisory_xact_lock`` serialises concurrent replicas (released on
      COMMIT/ROLLBACK). Under READ COMMITTED the loser's existence SELECTs then
      see the winner's committed rows and it seeds nothing.
    """
    # w2v-common 0.67.0 types the tenant as ``uuid.UUID | None`` end to end. The
    # ORM maps ``slide_templates.tenant_id`` with ``as_uuid=False``, so this repo
    # holds a ``str``; convert HERE, at the boundary, rather than widening the
    # column mapping. ``UUID(...)`` also fails loud on a malformed constant at
    # pod start instead of letting the driver reject it inside the seed txn.
    await bind_session_tenant(db, UUID(tenant_id), mode="enforce")
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SEED_ADVISORY_LOCK_KEY})


async def missing_system_templates(db: AsyncSession) -> set[str]:
    """Names in :data:`SYSTEM_TEMPLATE_NAMES` that are absent from the database.

    Read-only, and readable by any tenant (or none): ``tenant_isolation_read``
    exposes ``is_system`` rows unconditionally.
    """
    rows = await db.execute(select(SlideTemplate.name).where(SlideTemplate.is_system.is_(True)))
    return set(SYSTEM_TEMPLATE_NAMES) - set(rows.scalars().all())


#: Columns of an existing system template that :data:`SEED_TEMPLATES` owns.
#: ``template_id``/``tenant_id``/``name``/``is_system`` are identity and are never
#: rewritten; ``is_active`` is forced back to True because a system template that
#: drifted inactive silently breaks every deck that requests it.
_SYNCED_FIELDS = (
    "display_name",
    "category",
    "layout_type",
    "slot_definitions",
    "styling_rules",
    "default_transition",
    "default_timing_hint_seconds",
)


def _sync_existing(existing: SlideTemplate, template_data: Mapping[str, object]) -> bool:
    """Bring an already-seeded row back in line with the code. True if it changed.

    Without this the seed did ``if existing: continue``, so EVERY edit to a system
    template (slot schema, styling, transition, timing hint) was inert in every
    environment that had ever been seeded — and ``/health`` still reported the
    seed OK, because it only checks that the names are present.

    Only fields that actually differ are assigned, so an in-sync row emits no
    UPDATE and startup does not churn the table on every replica.
    """
    changed = False
    for field in _SYNCED_FIELDS:
        default = 10.0 if field == "default_timing_hint_seconds" else None
        desired = template_data.get(field, default)
        if getattr(existing, field, None) != desired:
            setattr(existing, field, desired)
            changed = True
    if not getattr(existing, "is_active", True):
        existing.is_active = True
        changed = True
    return changed


async def freeze_system_template_versions(db: AsyncSession) -> int:
    """Freeze every system template's CURRENT snapshot in ``template_versions``.

    This is the fix for the outage this module's siblings could not prevent.
    ``DeckBuilder._freeze_versions`` records what a deck was rendered against: it
    looks for a ``template_versions`` row whose ``snapshot_hash`` matches the
    template as it stands, and INSERTs one when there is none. That INSERT runs in
    the CALLER's transaction, under the caller's ``app.current_tenant``, and the
    ``tenant_isolation_ins`` policy on ``template_versions`` demands the parent
    template be OWNED by that tenant. A platform template never is. So the request
    path can read a system template but can NEVER freeze a version of one:

        (psycopg.errors.InsufficientPrivilege) new row violates row-level
        security policy for table "template_versions"

    which failed the whole deck job — and every consumer that soft-fails a deck
    render then shipped an empty deck. Two ways in, both live in production:

    * a system template that has never been frozen (13 of the 16 were in this
      state), so the FIRST render of it fails; and
    * an edit to a seeded template — :func:`_sync_existing` updates
      ``slide_templates`` but nothing updated its frozen version, so the stored
      hash goes stale and EVERY later render fails. Removing the undrawn ``logo``
      slot from the ``title`` template did exactly this and took out every promo
      deck downstream.

    Doing it here closes both. The seed transaction is already bound to
    :data:`SYSTEM_TEMPLATE_TENANT_ID` — the OWNER of these rows — so the same
    INSERT the request path is refused is permitted here, with no policy
    widening, no ``BYPASSRLS`` and no ``SECURITY DEFINER``. It runs in the SAME
    transaction as the sync above, so there is no window in which a template row
    is updated but its version is not.

    Idempotent by hash: a template whose current snapshot is already frozen emits
    no write, so this is a no-op on every start after the first. Returns the
    number of versions inserted.
    """
    # Expire first so the hashes are computed over values that have been through
    # JSONB, exactly as ``_freeze_versions`` sees them when it re-reads the
    # template at request time. Hashing the Python literals we just assigned
    # could round-trip differently and silently re-open the defect.
    db.expire_all()
    templates = (
        (await db.execute(select(SlideTemplate).where(SlideTemplate.is_system.is_(True))))
        .scalars()
        .all()
    )

    frozen = 0
    for template in templates:
        snapshot_hash = snapshot_hash_for(template)
        already = await db.execute(
            select(TemplateVersion.version_id).where(
                TemplateVersion.template_id == template.template_id,
                TemplateVersion.snapshot_hash == snapshot_hash,
            )
        )
        if already.scalar_one_or_none() is not None:
            continue

        current_max = (
            await db.execute(
                select(func.max(TemplateVersion.version_number)).where(
                    TemplateVersion.template_id == template.template_id
                )
            )
        ).scalar() or 0
        db.add(
            TemplateVersion(
                version_id=str(uuid7()),
                template_id=template.template_id,
                version_number=current_max + 1,
                snapshot_hash=snapshot_hash,
                slot_definitions=template.slot_definitions,
                styling_rules=template.styling_rules,
                default_transition=template.default_transition,
                default_timing_hint_seconds=template.default_timing_hint_seconds,
            )
        )
        frozen += 1

    if frozen:
        await db.flush()
    return frozen


async def seed_templates(db: AsyncSession, tenant_id: str = SYSTEM_TEMPLATE_TENANT_ID) -> int:
    """Seed system templates, updating any that drifted. Returns count INSERTED.

    The return value is deliberately the inserted count only (that is what
    ``src/asgi.py`` logs and what the RLS suite asserts); updates and frozen
    versions are logged separately.
    """
    await _prepare_seed_transaction(db, tenant_id)
    seeded = 0
    updated = 0

    for template_data in SEED_TEMPLATES:
        stmt = select(SlideTemplate).where(
            SlideTemplate.name == template_data["name"],
            SlideTemplate.is_system.is_(True),
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if _sync_existing(existing, template_data):
                updated += 1
            continue

        template = SlideTemplate(
            template_id=str(uuid7()),
            tenant_id=tenant_id,
            name=template_data["name"],
            display_name=template_data["display_name"],
            category=template_data["category"],
            layout_type=template_data["layout_type"],
            slot_definitions=template_data["slot_definitions"],
            styling_rules=template_data["styling_rules"],
            default_transition=template_data.get("default_transition"),
            default_timing_hint_seconds=template_data.get("default_timing_hint_seconds", 10.0),
            is_system=True,
            is_active=True,
        )
        db.add(template)
        seeded += 1

    if seeded or updated:
        await db.flush()
    if seeded:
        logger.info("Seeded %d system templates", seeded)
    if updated:
        logger.info("Updated %d drifted system templates from SEED_TEMPLATES", updated)

    # Unconditional, not `if seeded or updated`: the templates already in the
    # database when this ships are in sync with the code but have a stale or
    # absent frozen version, so the run that repairs them changes nothing above.
    frozen = await freeze_system_template_versions(db)
    if frozen:
        logger.info("Froze %d system template versions", frozen)

    return seeded
