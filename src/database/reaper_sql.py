"""Stuck-deck-job reaper SECURITY DEFINER resolvers — single source (prod + tests).

The multi-replica reaper (``src.services.deck_reaper``) sweeps ``deck_jobs`` rows
that a dead pod left wedged at ``status='processing'`` (the Redis lock TTL-expired
but nothing re-drove or failed the orphan). That sweep spans **all tenants**, but
the runtime connects as the restricted ``w2v_slide_app`` role under
``TENANT_RLS_MODE=enforce`` with no ``app.current_tenant`` GUC bound in a
background task — so a plain ``SELECT FROM deck_jobs`` fail-closes to zero rows,
and a plain ``UPDATE`` cannot touch another tenant's row.

Two ``SECURITY DEFINER`` functions bridge that gap, mirroring the orchestrator's
``orphan_runs()`` / ``run_tenant()`` pattern (``w2v_orchestrator/src/database/rls.py``):

* ``stuck_deck_jobs(p_stale_before timestamptz)`` — read resolver: returns every
  ``processing`` job whose ``updated_at`` is older than the cutoff, across all
  tenants. The reaper then per-row checks Redis lock-absence before acting.
* ``fail_stuck_deck_job(p_job_id text, p_reason text)`` — write resolver: flips a
  single still-``processing`` job to ``failed`` with a reason. Guarded by
  ``status='processing'`` so it is idempotent and can never clobber a job that has
  since completed or failed on its own.

Both are owned by the migration user (postgres superuser in prod → bypasses FORCE;
the table owner in CI/tests → bypasses ENABLE-not-FORCE) so their bodies see every
tenant. ``EXECUTE`` is revoked from PUBLIC and granted to ``w2v_slide_app`` only if
that role already exists (out-of-band role provisioning); the function owner/definer
can always execute its own function, so tests running as the owner are unaffected.

``SET search_path FROM CURRENT`` pins the search_path in effect when the function is
CREATED (the migration / create_all connection) — injection-safe for SECURITY
DEFINER. The bodies are static DDL over fixed column names; no user input reaches
them (S608 false positive, same posture as the RLS predicates in ``rls_policies``).

Single-sourced here so BOTH the alembic migration (prod) and the ``create_all``
``after_create`` event (tests) apply identical DDL and can't drift.
"""

from __future__ import annotations

from sqlalchemy import Connection, MetaData, event, text

# Stable, slide_service-private namespace for the reaper's brief inter-replica
# mutex (``pg_try_advisory_xact_lock``). Any bigint fits; this one embeds the
# service port (6312) so an operator hitting it in ``pg_locks`` recognises it.
REAPER_ADVISORY_LOCK_KEY = -7312006312

_FN_PREAMBLE_READ = "LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$ "
_FN_PREAMBLE_WRITE = "LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path FROM CURRENT AS $$ "

# Read resolver — cross-tenant scan of wedged jobs. Columns are table-qualified so
# the RETURNS TABLE output params never make an unqualified reference ambiguous.
_STUCK_FN = (
    "CREATE OR REPLACE FUNCTION stuck_deck_jobs(p_stale_before timestamptz) "
    # ``tenant_id uuid`` — a LANGUAGE sql body is validated at CREATE time against
    # the live column type (uuid since 0005_tenant_id_uuid). Declaring ``text`` here
    # would fail with "structure of query does not match function result type" and
    # silently kill the reaper loop. CREATE OR REPLACE cannot change a return type,
    # so 0005 DROPs before re-creating.
    "RETURNS TABLE (job_id text, tenant_id uuid, updated_at timestamptz) "
    f"{_FN_PREAMBLE_READ}"
    "SELECT dj.job_id, dj.tenant_id, dj.updated_at FROM deck_jobs dj "
    "WHERE dj.status = 'processing' AND dj.updated_at < p_stale_before $$"
)

# Write resolver — idempotent processing→failed transition for one job. Returns the
# affected-row count so the caller knows whether it actually reaped (a job that
# raced to completion first yields 0).
_FAIL_FN = (
    "CREATE OR REPLACE FUNCTION fail_stuck_deck_job(p_job_id text, p_reason text) "
    "RETURNS integer "
    f"{_FN_PREAMBLE_WRITE}"
    "WITH upd AS ("
    "UPDATE deck_jobs SET status = 'failed', error = p_reason, updated_at = now() "
    "WHERE job_id = p_job_id AND status = 'processing' RETURNING 1"
    ") SELECT count(*)::int FROM upd $$"
)

# Conditional grants: the restricted runtime role is created out-of-band, so guard
# with a role-existence check (no-op in tests where the role is absent and the owner
# executes anyway; effective in prod once the role lands).
_GRANT = (
    "DO $$ BEGIN "
    "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_slide_app') THEN "
    "GRANT EXECUTE ON FUNCTION stuck_deck_jobs(timestamptz) TO w2v_slide_app; "
    "GRANT EXECUTE ON FUNCTION fail_stuck_deck_job(text, text) TO w2v_slide_app; "
    "END IF; END $$"
)


def upgrade_statements() -> list[str]:
    """DDL to create the reaper resolvers (migration ``upgrade()`` + test after_create)."""
    return [
        _STUCK_FN,
        "REVOKE EXECUTE ON FUNCTION stuck_deck_jobs(timestamptz) FROM PUBLIC",
        _FAIL_FN,
        "REVOKE EXECUTE ON FUNCTION fail_stuck_deck_job(text, text) FROM PUBLIC",
        _GRANT,
    ]


def downgrade_statements() -> list[str]:
    """Reverse of :func:`upgrade_statements` (fully reversible)."""
    return [
        "DROP FUNCTION IF EXISTS stuck_deck_jobs(timestamptz)",
        "DROP FUNCTION IF EXISTS fail_stuck_deck_job(text, text)",
    ]


def register_reaper_functions(metadata: MetaData) -> None:
    """Create the resolvers after ``create_all`` (tests/dev), single-sourced with prod.

    Listens on ``after_create`` of the whole metadata so it fires once every table
    (``deck_jobs`` in particular) exists — the function bodies reference it.
    """

    def _apply(target: object, connection: Connection, **kw: object) -> None:
        for stmt in upgrade_statements():
            connection.execute(text(stmt))

    event.listen(metadata, "after_create", _apply)
