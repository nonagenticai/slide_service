"""Align chain output with prod schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26 23:30:00.000000

Round-9 chain reconciliation. After Round 7 stamped prod at 0001,
querying information_schema on the live prod DB shows:

- Zero column_default on `status`, `is_system`, `is_active` (chain emits
  defaults via 0001's `server_default="pending"|"false"|"true"`).
- created_at/updated_at NOT NULL with `now()` default everywhere (chain
  emits nullable=True for the same columns).
- No `deck_jobs_job_id_key`, `slide_templates_template_id_key`, or
  `template_versions_version_id_key` UniqueConstraints (chain emits all
  three via `sa.UniqueConstraint(...)` in 0001).

Prod was bootstrapped by `Base.metadata.create_all` from an older
models.py; the chain captures a later state. This revision aligns
chain output with prod via idempotent raw SQL — no-op on prod,
corrective on fresh CI / test-namespace DBs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop redundant UniqueConstraints — the unique INDEXes still enforce.
    op.execute("ALTER TABLE deck_jobs DROP CONSTRAINT IF EXISTS deck_jobs_job_id_key")
    op.execute(
        "ALTER TABLE slide_templates DROP CONSTRAINT IF EXISTS slide_templates_template_id_key"
    )
    op.execute(
        "ALTER TABLE template_versions DROP CONSTRAINT IF EXISTS template_versions_version_id_key"
    )

    # 2. Drop DDL-level defaults on boolean / status cols; Python-side
    #    `default=` in models.py continues to fill values on insert.
    op.execute("ALTER TABLE deck_jobs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN is_system DROP DEFAULT")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN is_active DROP DEFAULT")

    # 3. SET NOT NULL on timestamp columns to match prod. server_default=now()
    #    means existing rows always have a value, so adding NOT NULL is safe.
    op.execute("ALTER TABLE deck_jobs ALTER COLUMN created_at SET NOT NULL")
    op.execute("ALTER TABLE deck_jobs ALTER COLUMN updated_at SET NOT NULL")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN created_at SET NOT NULL")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN updated_at SET NOT NULL")
    op.execute("ALTER TABLE template_versions ALTER COLUMN created_at SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE template_versions ALTER COLUMN created_at DROP NOT NULL")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN updated_at DROP NOT NULL")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN created_at DROP NOT NULL")
    op.execute("ALTER TABLE deck_jobs ALTER COLUMN updated_at DROP NOT NULL")
    op.execute("ALTER TABLE deck_jobs ALTER COLUMN created_at DROP NOT NULL")

    op.execute("ALTER TABLE slide_templates ALTER COLUMN is_active SET DEFAULT true")
    op.execute("ALTER TABLE slide_templates ALTER COLUMN is_system SET DEFAULT false")
    op.execute("ALTER TABLE deck_jobs ALTER COLUMN status SET DEFAULT 'pending'")

    op.execute(
        "ALTER TABLE template_versions ADD CONSTRAINT template_versions_version_id_key UNIQUE (version_id)"
    )
    op.execute(
        "ALTER TABLE slide_templates ADD CONSTRAINT slide_templates_template_id_key UNIQUE (template_id)"
    )
    op.execute("ALTER TABLE deck_jobs ADD CONSTRAINT deck_jobs_job_id_key UNIQUE (job_id)")
