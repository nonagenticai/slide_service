"""Stuck-deck-job reaper SECURITY DEFINER resolvers.

Adds the two ``SECURITY DEFINER`` functions the multi-replica reaper needs once
the runtime connects as the restricted ``w2v_slide_app`` role under
``TENANT_RLS_MODE=enforce``:

* ``stuck_deck_jobs(timestamptz)`` — cross-tenant read of ``processing`` jobs older
  than a cutoff (a plain ``SELECT`` would fail-closed to 0 rows under enforce).
* ``fail_stuck_deck_job(text, text)`` — idempotent processing→failed transition for
  one orphaned job.

EXECUTE is revoked from PUBLIC and granted to ``w2v_slide_app`` only if that role
already exists. Fully reversible.

⚠️ PINNED TO LITERAL ERA-CORRECT DDL — DO NOT RE-INTRODUCE THE IMPORT.
------------------------------------------------------------------------
This revision used to render its DDL by calling
``src.database.reaper_sql.upgrade_statements()``. Same fresh-database defect as
``0003_rls``, in the variant that carries no ``Base.metadata`` and no model import
at all: a ``LANGUAGE sql`` body is validated AT CREATE TIME against the live
column types. ``reaper_sql`` now declares ``RETURNS TABLE(..., tenant_id uuid, ...)``
because ``0005_tenant_id_uuid`` flipped the column. Replaying THIS revision on an
empty database — where ``deck_jobs.tenant_id`` is still ``varchar(255)`` — would
die with a return-type mismatch, so ``alembic upgrade head`` could never get past
0004 on a fresh DB.

The DDL below is what this revision emitted IN ITS OWN ERA: ``tenant_id text`` in
the ``RETURNS TABLE`` (binary-coercible from the then-``varchar`` column). It is
frozen. ``0005_tenant_id_uuid`` DROPs and re-CREATEs these with the ``uuid``
signature — ``CREATE OR REPLACE`` cannot change a return type.

Revision ID: 0004_reaper_fns
Revises: 0003_rls
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_reaper_fns"
down_revision: str | None = "0003_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Era-correct signatures: ``tenant_id text``, matching the varchar(255) column as
# it stood at this revision.
_STUCK_FN = (
    "CREATE OR REPLACE FUNCTION stuck_deck_jobs(p_stale_before timestamptz) "
    "RETURNS TABLE (job_id text, tenant_id text, updated_at timestamptz) "
    "LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$ "
    "SELECT dj.job_id, dj.tenant_id, dj.updated_at FROM deck_jobs dj "
    "WHERE dj.status = 'processing' AND dj.updated_at < p_stale_before $$"
)

_FAIL_FN = (
    "CREATE OR REPLACE FUNCTION fail_stuck_deck_job(p_job_id text, p_reason text) "
    "RETURNS integer "
    "LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path FROM CURRENT AS $$ "
    "WITH upd AS ("
    "UPDATE deck_jobs SET status = 'failed', error = p_reason, updated_at = now() "
    "WHERE job_id = p_job_id AND status = 'processing' RETURNING 1"
    ") SELECT count(*)::int FROM upd $$"
)

_GRANT = (
    "DO $$ BEGIN "
    "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_slide_app') THEN "
    "GRANT EXECUTE ON FUNCTION stuck_deck_jobs(timestamptz) TO w2v_slide_app; "
    "GRANT EXECUTE ON FUNCTION fail_stuck_deck_job(text, text) TO w2v_slide_app; "
    "END IF; END $$"
)

_UPGRADE_STATEMENTS = (
    _STUCK_FN,
    "REVOKE EXECUTE ON FUNCTION stuck_deck_jobs(timestamptz) FROM PUBLIC",
    _FAIL_FN,
    "REVOKE EXECUTE ON FUNCTION fail_stuck_deck_job(text, text) FROM PUBLIC",
    _GRANT,
)

_DOWNGRADE_STATEMENTS = (
    "DROP FUNCTION IF EXISTS stuck_deck_jobs(timestamptz)",
    "DROP FUNCTION IF EXISTS fail_stuck_deck_job(text, text)",
)


def upgrade() -> None:
    for stmt in _UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE_STATEMENTS:
        op.execute(stmt)
