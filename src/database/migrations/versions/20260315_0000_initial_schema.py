"""Initial schema for slide_service.

Revision ID: 0001
Revises:
Create Date: 2026-03-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slide_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("layout_type", sa.String(50), nullable=False),
        sa.Column(
            "slot_definitions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "styling_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "default_transition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("default_timing_hint_seconds", sa.Float(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id"),
    )
    op.create_index(
        op.f("ix_slide_templates_template_id"),
        "slide_templates",
        ["template_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_slide_templates_tenant_id"),
        "slide_templates",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "template_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("template_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "slot_definitions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "styling_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "default_transition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("default_timing_hint_seconds", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id"),
    )
    op.create_index(
        op.f("ix_template_versions_version_id"),
        "template_versions",
        ["version_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_template_versions_template_id"),
        "template_versions",
        ["template_id"],
        unique=False,
    )

    op.create_table(
        "deck_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("pptx_storage_path", sa.String(500), nullable=True),
        sa.Column("slide_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(op.f("ix_deck_jobs_job_id"), "deck_jobs", ["job_id"], unique=True)
    op.create_index(op.f("ix_deck_jobs_tenant_id"), "deck_jobs", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_deck_jobs_tenant_id"), table_name="deck_jobs")
    op.drop_index(op.f("ix_deck_jobs_job_id"), table_name="deck_jobs")
    op.drop_table("deck_jobs")
    op.drop_index(op.f("ix_template_versions_template_id"), table_name="template_versions")
    op.drop_index(op.f("ix_template_versions_version_id"), table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_index(op.f("ix_slide_templates_tenant_id"), table_name="slide_templates")
    op.drop_index(op.f("ix_slide_templates_template_id"), table_name="slide_templates")
    op.drop_table("slide_templates")
