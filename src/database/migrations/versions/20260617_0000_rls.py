"""Phase D RLS — custom tenant-isolation policies for slide_service.

* ``deck_jobs`` — strict own-only.
* ``slide_templates`` — read own-or-``'system'`` sentinel, write own-only.
* ``template_versions`` — visibility derived from the parent ``slide_templates`` row
  (no tenant_id column of its own).

ENABLE-not-FORCE → inert until the runtime connects as the restricted
``w2v_slide_app`` role with ``TENANT_RLS_MODE=enforce``. Fully reversible.

⚠️ PINNED TO LITERAL ERA-CORRECT DDL — DO NOT RE-INTRODUCE THE IMPORT.
------------------------------------------------------------------------
This revision used to render its DDL by calling
``src.database.rls_policies.upgrade_statements()``. That is a fresh-database
defect: a migration must not render DDL from live CODE of any kind (model
metadata, an RLS generator, a resolver module). ``rls_policies`` now emits a
``::uuid`` GUC cast and an ``OR is_system`` clause, because ``0005_tenant_id_uuid``
flipped ``tenant_id`` to a native ``uuid``. Replaying THIS revision on an empty
database — where ``tenant_id`` is still ``varchar(255)`` and no such comparison is
valid — would die with ``operator does not exist: character varying = uuid``, so
``alembic upgrade head`` could never get past 0003 on a fresh DB.

The statements below are therefore what this revision emitted IN ITS OWN ERA:
a plain TEXT GUC (no cast) and the ``tenant_id = 'system'`` sentinel predicate.
They are frozen. ``0005_tenant_id_uuid`` is what converts them to the uuid form.

The DOWNGRADE deliberately drops EVERY policy name this migration could ever have
emitted across code generations (``tenant_isolation`` plus the ``_read``/``_ins``/
``_upd``/``_del`` split) and then disables RLS, so a database provisioned by an
older code path still reverses cleanly.

Revision ID: 0003_rls
Revises: 0002
"""

from alembic import op

revision: str = "0003_rls"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None

# Era-correct GUC: TEXT compare, deliberately NO ``::uuid`` cast — at this
# revision ``tenant_id`` is ``varchar(255)``.
_GUC = "NULLIF(current_setting('app.current_tenant', true), '')"

_TABLES = ("deck_jobs", "slide_templates", "template_versions")

# Every policy name any generation of the helper has emitted, per table. The
# downgrade drops all of them unconditionally (IF EXISTS) so an older-provisioned
# database reverses cleanly.
_ALL_POLICY_NAMES = (
    "tenant_isolation",
    "tenant_isolation_read",
    "tenant_isolation_ins",
    "tenant_isolation_upd",
    "tenant_isolation_del",
)

_PARENT_VISIBLE = (
    "EXISTS (SELECT 1 FROM slide_templates t "
    "WHERE t.template_id = template_versions.template_id "
    f"AND (t.tenant_id = {_GUC} OR t.tenant_id = 'system'))"
)
_PARENT_OWNED = (
    "EXISTS (SELECT 1 FROM slide_templates t "
    "WHERE t.template_id = template_versions.template_id "
    f"AND t.tenant_id = {_GUC})"
)

_UPGRADE_STATEMENTS = (
    # deck_jobs — strict own-only.
    "ALTER TABLE deck_jobs ENABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON deck_jobs",
    f"CREATE POLICY tenant_isolation ON deck_jobs "
    f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})",
    # slide_templates — read own-or-'system' sentinel; write own-only.
    "ALTER TABLE slide_templates ENABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON slide_templates",
    "DROP POLICY IF EXISTS tenant_isolation_read ON slide_templates",
    "DROP POLICY IF EXISTS tenant_isolation_ins ON slide_templates",
    "DROP POLICY IF EXISTS tenant_isolation_upd ON slide_templates",
    "DROP POLICY IF EXISTS tenant_isolation_del ON slide_templates",
    f"CREATE POLICY tenant_isolation_read ON slide_templates FOR SELECT "
    f"USING (tenant_id = {_GUC} OR tenant_id = 'system')",
    f"CREATE POLICY tenant_isolation_ins ON slide_templates FOR INSERT "
    f"WITH CHECK (tenant_id = {_GUC})",
    f"CREATE POLICY tenant_isolation_upd ON slide_templates FOR UPDATE "
    f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})",
    f"CREATE POLICY tenant_isolation_del ON slide_templates FOR DELETE USING (tenant_id = {_GUC})",
    # template_versions — ownership derived from the parent slide_templates row.
    "ALTER TABLE template_versions ENABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON template_versions",
    "DROP POLICY IF EXISTS tenant_isolation_read ON template_versions",
    "DROP POLICY IF EXISTS tenant_isolation_ins ON template_versions",
    "DROP POLICY IF EXISTS tenant_isolation_upd ON template_versions",
    "DROP POLICY IF EXISTS tenant_isolation_del ON template_versions",
    f"CREATE POLICY tenant_isolation_read ON template_versions FOR SELECT "
    f"USING ({_PARENT_VISIBLE})",
    f"CREATE POLICY tenant_isolation_ins ON template_versions FOR INSERT "
    f"WITH CHECK ({_PARENT_OWNED})",
    f"CREATE POLICY tenant_isolation_upd ON template_versions FOR UPDATE "
    f"USING ({_PARENT_OWNED}) WITH CHECK ({_PARENT_OWNED})",
    f"CREATE POLICY tenant_isolation_del ON template_versions FOR DELETE USING ({_PARENT_OWNED})",
)


def upgrade() -> None:
    for stmt in _UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for table in _TABLES:
        for policy in _ALL_POLICY_NAMES:
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
