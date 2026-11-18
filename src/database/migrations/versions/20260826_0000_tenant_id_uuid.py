"""Flip ``tenant_id`` from ``varchar(255)`` to native ``uuid``; retire the ``'system'`` sentinel.

Fleet-wide ruling: ``tenant_id`` is a real registered tenant uuid on EVERY row —
never text, never a sentinel.

slide_service carried the sentinel ``tenant_id = 'system'`` on all 16 platform
templates, and it MEANT two different things at once: "who owns this row" and
"every tenant may read this row". Those are now separated:

* **Ownership** → ``tenant_id``, a real uuid. The seeded platform templates are
  owned by ``0195f48b-d000-7000-8000-000000000099`` (registered in
  ``licenses.tenants`` as ``fr-test-user``).
* **Cross-tenant visibility** → the ``is_system`` BOOLEAN that ``slide_templates``
  already had. Read paths become ``tenant_id = <caller> OR is_system`` in BOTH
  layers (``src/api/routes.py`` and the RLS policies). Dropping the sentinel
  without this would take every tenant's template list to zero.

``deck_jobs.tenant_id`` (all values already valid uuids) flips type in the same
revision so the two tenant-scoped columns cannot drift apart.

Ordering is load-bearing — five things must happen in this exact sequence:

1. **Set ``is_system`` while the sentinel is still readable.** Once ``tenant_id``
   is ``uuid`` the string ``'system'`` can no longer be matched or written, so the
   visibility flag must be lifted off it first.
2. **Re-own the sentinel rows** to the real tenant uuid, still as text.
3. **Fail loud on anything still unparseable.** A ``DO`` block counts rows that are
   NOT NULL and do not match the uuid regex and ``RAISE EXCEPTION`` with the count
   and the first offending value. We deliberately do NOT hide them behind a
   ``WHERE tenant_id ~ ...`` on the ALTER — a silent skip would either drop rows on
   the floor or leave the migration half-applied.
4. **Drop the policies and the resolver functions BEFORE the ALTER.** Postgres
   refuses ``ALTER COLUMN ... TYPE`` outright on a column referenced by a policy
   expression ("cannot alter type of a column used in a policy definition"), and the
   ``SECURITY DEFINER`` resolver ``stuck_deck_jobs`` declares
   ``RETURNS TABLE(..., tenant_id text)`` — after the ALTER its body returns ``uuid``
   and every call dies with "structure of query does not match function result
   type", silently killing the reaper loop. ``CREATE OR REPLACE`` cannot change a
   return type, so it must be DROPped.
5. **ALTER, then re-create policies + resolvers** in their uuid form.

All DDL below is spelled out LITERALLY rather than rendered from
``src.database.rls_policies`` / ``src.database.reaper_sql``. A migration must not
render DDL from live code of any kind: the moment those modules change again, a
fresh-database replay of THIS revision would emit tomorrow's DDL against today's
schema. (``0003_rls`` and ``0004_reaper_fns`` were pinned for exactly this reason —
see their docstrings.) The statements here are byte-equivalent to what those
modules emit today; the modules stay the single source for the ``create_all``
test path.

The downgrade returns both columns to ``varchar(255)`` and restores the legacy
text-comparison policies and the ``tenant_id text`` resolver, spelled out
literally. NOTE: the sentinel→uuid re-ownership is deliberately NOT reversed —
the uuid is the correct canonical owner and re-introducing ``'system'`` would only
recreate the defect. ``is_system`` is likewise left set, since the legacy read
policy is an OR and tolerates it.

Revision ID: 0005_tenant_id_uuid
Revises: 0004_reaper_fns
Create Date: 2026-08-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tenant_id_uuid"
down_revision: str | None = "0004_reaper_fns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Retired sentinel that used to mean both "platform-owned" and "world-readable".
LEGACY_SYSTEM_SENTINEL = "system"

#: Real registered tenant that now OWNS the platform templates. Verified present in
#: ``licenses.tenants`` as ``fr-test-user``.
SYSTEM_TEMPLATE_TENANT_UUID = "0195f48b-d000-7000-8000-000000000099"

#: The two tenant-scoped columns. ``template_versions`` has no ``tenant_id``.
TENANT_TABLES = ("slide_templates", "deck_jobs")

#: All three RLS-enabled tables (``template_versions``' policies reference
#: ``slide_templates.tenant_id``, so they must be dropped before the ALTER too).
RLS_TABLES = ("deck_jobs", "slide_templates", "template_versions")

#: Every policy name any generation of the helper has emitted. Dropped
#: unconditionally so a database provisioned by an older code path reverses cleanly.
ALL_POLICY_NAMES = (
    "tenant_isolation",
    "tenant_isolation_read",
    "tenant_isolation_ins",
    "tenant_isolation_upd",
    "tenant_isolation_del",
)

_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# Legacy (pre-0005) GUC: text compare, no cast. Restored verbatim on downgrade.
_GUC_TEXT = "NULLIF(current_setting('app.current_tenant', true), '')"
# Post-0005 GUC: cast to uuid. NULLIF keeps an unbound/empty GUC NULL (fail-closed)
# rather than raising an invalid-uuid cast error.
_GUC_UUID = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


# --------------------------------------------------------------------------- #
# Guard — FAIL LOUD, never skip.
# --------------------------------------------------------------------------- #
# Interpolated only with names from TENANT_TABLES (module-level constants) and the
# uuid regex — never with anything caller- or row-derived.
_GUARD_TEMPLATE = """
DO $$
DECLARE
  bad_count bigint;
  sample text;
BEGIN
  SELECT count(*), min(tenant_id) INTO bad_count, sample
    FROM {table}
   WHERE tenant_id IS NOT NULL AND tenant_id !~ '{uuid_re}';
  IF bad_count > 0 THEN
    RAISE EXCEPTION
      '{table}.tenant_id: % row(s) hold a value that is not a parseable uuid '
      '(first offender: %). Refusing to ALTER {table}.tenant_id to uuid. Those rows '
      'were NEITHER dropped NOR skipped -- the migration aborted with the data '
      'exactly as it was. Each value is a tenant NAME (or worse, a sentinel): look '
      'it up in licenses.tenants, add an explicit UPDATE mapping it to that '
      'tenant''s uuid ABOVE this guard, then re-run. Do NOT make this pass by '
      'adding a WHERE filter to the ALTER -- that silently strands the rows.',
      bad_count, sample;
  END IF;
END $$
"""


def _guard_sql(table: str) -> str:
    """FAIL LOUD if any non-NULL ``tenant_id`` in ``table`` is not a parseable uuid."""
    return _GUARD_TEMPLATE.format(table=table, uuid_re=_UUID_RE)


# --------------------------------------------------------------------------- #
# Policy DDL — literal, both eras.
# --------------------------------------------------------------------------- #
def _policy_statements(guc: str, *, system_predicate: str) -> tuple[str, ...]:
    """Render the full policy set for one era.

    ``guc`` is the tenant GUC expression (text or ``::uuid``); ``system_predicate``
    is how "visible to every tenant" was expressed in that era — the legacy
    ``tenant_id = 'system'`` sentinel or the current ``is_system`` flag. Both call
    sites pass module-level constants; nothing here is caller-derived.
    """
    parent_visible = (
        "EXISTS (SELECT 1 FROM slide_templates t "  # nosec B608 - interpolates SQL structure (GUC expr/type), never a value
        "WHERE t.template_id = template_versions.template_id "
        f"AND (t.tenant_id = {guc} OR t.{system_predicate}))"
    )
    parent_owned = (
        "EXISTS (SELECT 1 FROM slide_templates t "  # nosec B608 - interpolates SQL structure (GUC expr/type), never a value
        "WHERE t.template_id = template_versions.template_id "
        f"AND t.tenant_id = {guc})"
    )
    return (
        # deck_jobs — strict own-only (no system rows; a deck job always has an owner).
        "ALTER TABLE deck_jobs ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_isolation ON deck_jobs",
        f"CREATE POLICY tenant_isolation ON deck_jobs "
        f"USING (tenant_id = {guc}) WITH CHECK (tenant_id = {guc})",
        # slide_templates — read own-or-system; write own-only. ``is_system`` is
        # absent from every WITH CHECK, so a tenant can never mint a platform row.
        "ALTER TABLE slide_templates ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_isolation ON slide_templates",
        "DROP POLICY IF EXISTS tenant_isolation_read ON slide_templates",
        "DROP POLICY IF EXISTS tenant_isolation_ins ON slide_templates",
        "DROP POLICY IF EXISTS tenant_isolation_upd ON slide_templates",
        "DROP POLICY IF EXISTS tenant_isolation_del ON slide_templates",
        f"CREATE POLICY tenant_isolation_read ON slide_templates FOR SELECT "
        f"USING (tenant_id = {guc} OR {system_predicate})",
        f"CREATE POLICY tenant_isolation_ins ON slide_templates FOR INSERT "
        f"WITH CHECK (tenant_id = {guc})",
        f"CREATE POLICY tenant_isolation_upd ON slide_templates FOR UPDATE "
        f"USING (tenant_id = {guc}) WITH CHECK (tenant_id = {guc})",
        f"CREATE POLICY tenant_isolation_del ON slide_templates FOR DELETE "
        f"USING (tenant_id = {guc})",
        # template_versions — ownership derived from the parent slide_templates row.
        "ALTER TABLE template_versions ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_isolation ON template_versions",
        "DROP POLICY IF EXISTS tenant_isolation_read ON template_versions",
        "DROP POLICY IF EXISTS tenant_isolation_ins ON template_versions",
        "DROP POLICY IF EXISTS tenant_isolation_upd ON template_versions",
        "DROP POLICY IF EXISTS tenant_isolation_del ON template_versions",
        f"CREATE POLICY tenant_isolation_read ON template_versions FOR SELECT "
        f"USING ({parent_visible})",
        f"CREATE POLICY tenant_isolation_ins ON template_versions FOR INSERT "
        f"WITH CHECK ({parent_owned})",
        f"CREATE POLICY tenant_isolation_upd ON template_versions FOR UPDATE "
        f"USING ({parent_owned}) WITH CHECK ({parent_owned})",
        f"CREATE POLICY tenant_isolation_del ON template_versions FOR DELETE "
        f"USING ({parent_owned})",
    )


# Post-0005: uuid GUC cast + the ``is_system`` flag.
_UUID_ERA_POLICIES = _policy_statements(_GUC_UUID, system_predicate="is_system")
# Pre-0005: text GUC + the ``'system'`` sentinel. Exactly what 0003_rls created.
_TEXT_ERA_POLICIES = _policy_statements(_GUC_TEXT, system_predicate="tenant_id = 'system'")

_DROP_POLICIES = tuple(
    stmt
    for table in RLS_TABLES
    for stmt in (*(f"DROP POLICY IF EXISTS {p} ON {table}" for p in ALL_POLICY_NAMES),)
)


# --------------------------------------------------------------------------- #
# Resolver DDL — literal, both eras. DROP + CREATE: a return type cannot be
# changed by CREATE OR REPLACE, and ALTER TABLE does not revalidate function
# bodies, so a stale ``text`` signature would survive the ALTER and fail at
# RUNTIME inside the reaper loop.
# --------------------------------------------------------------------------- #
def _resolver_statements(tenant_sql_type: str) -> tuple[str, ...]:
    """Render the reaper resolvers for one era, carrying the REVOKE/GRANT with them.

    ``tenant_sql_type`` is a module-level literal (``"uuid"`` or ``"text"``).
    ``fail_stuck_deck_job`` is era-independent (it never touches ``tenant_id``) but
    is re-emitted alongside so the grant block stays a single unit.
    """
    return (
        "DROP FUNCTION IF EXISTS stuck_deck_jobs(timestamptz)",
        "DROP FUNCTION IF EXISTS fail_stuck_deck_job(text, text)",
        "CREATE FUNCTION stuck_deck_jobs(p_stale_before timestamptz) "  # nosec B608 - interpolates SQL structure (GUC expr/type), never a value
        f"RETURNS TABLE (job_id text, tenant_id {tenant_sql_type}, updated_at timestamptz) "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$ "
        "SELECT dj.job_id, dj.tenant_id, dj.updated_at FROM deck_jobs dj "
        "WHERE dj.status = 'processing' AND dj.updated_at < p_stale_before $$",
        "REVOKE EXECUTE ON FUNCTION stuck_deck_jobs(timestamptz) FROM PUBLIC",
        "CREATE FUNCTION fail_stuck_deck_job(p_job_id text, p_reason text) "
        "RETURNS integer "
        "LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path FROM CURRENT AS $$ "
        "WITH upd AS ("
        "UPDATE deck_jobs SET status = 'failed', error = p_reason, updated_at = now() "
        "WHERE job_id = p_job_id AND status = 'processing' RETURNING 1"
        ") SELECT count(*)::int FROM upd $$",
        "REVOKE EXECUTE ON FUNCTION fail_stuck_deck_job(text, text) FROM PUBLIC",
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_slide_app') THEN "
        "GRANT EXECUTE ON FUNCTION stuck_deck_jobs(timestamptz) TO w2v_slide_app; "
        "GRANT EXECUTE ON FUNCTION fail_stuck_deck_job(text, text) TO w2v_slide_app; "
        "END IF; END $$",
    )


_DROP_RESOLVERS = (
    "DROP FUNCTION IF EXISTS stuck_deck_jobs(timestamptz)",
    "DROP FUNCTION IF EXISTS fail_stuck_deck_job(text, text)",
)


def upgrade() -> None:
    # 1. Lift cross-tenant visibility off the sentinel and onto is_system, while
    #    the sentinel string is still matchable.
    # Bound parameter, not an f-string: the value is a module constant today,
    # but a literal-interpolated UPDATE is the shape bandit flags (B608) and the
    # shape that becomes a real injection the moment someone parameterises it.
    op.execute(
        sa.text(
            "UPDATE slide_templates SET is_system = true WHERE tenant_id = :sentinel"
        ).bindparams(sentinel=LEGACY_SYSTEM_SENTINEL),
    )

    # 2. Re-own those rows to the real registered tenant, still as text.
    op.execute(
        sa.text(
            "UPDATE slide_templates SET tenant_id = :new_owner WHERE tenant_id = :sentinel"
        ).bindparams(new_owner=SYSTEM_TEMPLATE_TENANT_UUID, sentinel=LEGACY_SYSTEM_SENTINEL),
    )

    # 3. Fail loud on anything still unparseable — never silently skipped.
    for table in TENANT_TABLES:
        op.execute(_guard_sql(table))

    # 4. Drop policies (Postgres refuses the ALTER while they reference the column)
    #    and the resolvers (their text return type would break at runtime).
    for stmt in _DROP_POLICIES:
        op.execute(stmt)
    for stmt in _DROP_RESOLVERS:
        op.execute(stmt)

    # 5. The type flip.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE uuid USING tenant_id::uuid")

    # 6. Re-create in uuid form: ::uuid GUC cast + ``OR is_system``.
    for stmt in _UUID_ERA_POLICIES:
        op.execute(stmt)
    for stmt in _resolver_statements("uuid"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DROP_POLICIES:
        op.execute(stmt)
    for stmt in _DROP_RESOLVERS:
        op.execute(stmt)

    for table in TENANT_TABLES:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE varchar(255) USING tenant_id::text"
        )

    # Restore the legacy text-comparison policies 0003_rls originally created,
    # spelled out LITERALLY (see the module docstring: a downgrade can never render
    # from the live modules, which now emit ``::uuid`` against a varchar column and
    # would fail with "operator does not exist: character varying = uuid").
    for stmt in _TEXT_ERA_POLICIES:
        op.execute(stmt)
    for stmt in _resolver_statements("text"):
        op.execute(stmt)
