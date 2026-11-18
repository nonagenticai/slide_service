"""SQLAlchemy ORM models for slide_service."""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""


class SlideTemplate(Base):
    """Template definition with slot schemas and styling rules."""

    __tablename__ = "slide_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    # Native ``uuid`` (0005_tenant_id_uuid). ``as_uuid=False`` keeps the Python side
    # ``str`` so every caller (request identity, RLS GUC, seeder) stays string-typed;
    # the DB column is a real uuid, so a sentinel like the retired ``'system'`` can
    # no longer be written at all. Platform visibility lives on ``is_system``.
    tenant_id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), index=True)
    name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    layout_type: Mapped[str] = mapped_column(String(50))
    slot_definitions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    styling_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    default_transition: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    default_timing_hint_seconds: Mapped[float | None] = mapped_column(default=10.0)
    is_system: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TemplateVersion(Base):
    """Frozen template snapshot for reproducibility."""

    __tablename__ = "template_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    # FK to the template this is a frozen snapshot OF (migration
    # 0006_template_versions_fk). RESTRICT, not CASCADE: a template with frozen
    # versions must not be deletable, because those versions are the
    # reproducibility record for decks already rendered from it.
    template_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("slide_templates.template_id", ondelete="RESTRICT", onupdate="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column()
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    slot_definitions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    styling_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    default_transition: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    default_timing_hint_seconds: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DeckJob(Base):
    """Async deck generation job tracking."""

    __tablename__ = "deck_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    # Native ``uuid`` (0005_tenant_id_uuid). ``as_uuid=False`` keeps the Python side
    # ``str`` so every caller (request identity, RLS GUC, seeder) stays string-typed;
    # the DB column is a real uuid, so a sentinel like the retired ``'system'`` can
    # no longer be written at all. Platform visibility lives on ``is_system``.
    tenant_id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    pptx_storage_path: Mapped[str | None] = mapped_column(String(500), default=None)
    slide_count: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Phase D RLS: custom tenant-isolation policies (own-or-system templates +
# parent-derived version visibility + strict deck_jobs). Registered as an
# after_create event so tests' create_all matches the 0003_rls migration. Inert
# until the runtime connects as the restricted w2v_slide_app role with
# TENANT_RLS_MODE=enforce.
from src.database.rls_policies import register_rls_policies  # noqa: E402

register_rls_policies(Base.metadata)

# Reaper SECURITY DEFINER resolvers (stuck_deck_jobs / fail_stuck_deck_job).
# Registered as an after_create event so tests' create_all carries the same
# functions the alembic migration installs in prod (single-sourced).
from src.database.reaper_sql import register_reaper_functions  # noqa: E402

register_reaper_functions(Base.metadata)
