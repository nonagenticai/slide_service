"""Phase D RLS — custom tenant-isolation policies for slide_service (single source).

slide_service can't use the generic ``w2v_common`` per-table policies because two of
its three tenant-scoped tables are non-standard:

* ``deck_jobs`` — ordinary strict isolation (``tenant_id = <GUC>``).
* ``slide_templates`` — platform templates are shared via the ``is_system``
  BOOLEAN column (the app reads ``tenant_id = <caller> OR is_system``). The
  retired ``tenant_id = 'system'`` sentinel is GONE: ``tenant_id`` is a native
  ``uuid`` carrying the real registered tenant on every row (0005_tenant_id_uuid).
  Reads are **own-or-system** but writes stay **own-only** (a tenant must not
  create/modify platform templates — ``is_system`` is not in any WITH CHECK, so a
  tenant can only ever insert rows it owns).
* ``template_versions`` — has NO ``tenant_id`` of its own; ownership is derived from
  its parent ``slide_templates`` row (FK on ``template_id``). A row is visible iff its
  parent template is visible (own-or-system); writable iff the parent is owned. The
  EXISTS sub-select runs under ``slide_templates``' own RLS, which already filters to
  own-or-system — no recursion (slide_templates never references template_versions).

All predicates use the same fail-closed GUC the w2v_common request/background
paths bind (``bind_session_tenant``), cast ``::uuid`` because ``tenant_id`` is now a
native ``uuid`` column. ENABLE-not-FORCE → inert until the runtime connects as the restricted
``w2v_slide_app`` role with ``TENANT_RLS_MODE=enforce``.

Single-sourced here so BOTH the alembic migration (prod) and the ``create_all``
``after_create`` event (tests) apply identical policies.
"""

from __future__ import annotations

from sqlalchemy import Connection, MetaData, event, text

POLICY_NAME = "tenant_isolation"
# Fail-closed transaction-scoped GUC (unset → NULL → matches nothing). Cast to
# ``uuid`` because tenant_id is a native uuid column as of 0005_tenant_id_uuid;
# NULLIF keeps an unbound/empty GUC NULL rather than a cast error.
_GUC = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"

# deck_jobs — strict own-only.
_DECK = [
    "ALTER TABLE deck_jobs ENABLE ROW LEVEL SECURITY",
    f"DROP POLICY IF EXISTS {POLICY_NAME} ON deck_jobs",
    f"CREATE POLICY {POLICY_NAME} ON deck_jobs "
    f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})",
]

# slide_templates — read own-or-``is_system``; write own-only.
_TEMPLATES = [
    "ALTER TABLE slide_templates ENABLE ROW LEVEL SECURITY",
    f"DROP POLICY IF EXISTS {POLICY_NAME} ON slide_templates",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_read ON slide_templates",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_ins ON slide_templates",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_upd ON slide_templates",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_del ON slide_templates",
    f"CREATE POLICY {POLICY_NAME}_read ON slide_templates FOR SELECT "
    f"USING (tenant_id = {_GUC} OR is_system)",
    f"CREATE POLICY {POLICY_NAME}_ins ON slide_templates FOR INSERT "
    f"WITH CHECK (tenant_id = {_GUC})",
    f"CREATE POLICY {POLICY_NAME}_upd ON slide_templates FOR UPDATE "
    f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})",
    f"CREATE POLICY {POLICY_NAME}_del ON slide_templates FOR DELETE USING (tenant_id = {_GUC})",
]

# template_versions — ownership derived from the parent slide_templates row.
_PARENT_VISIBLE = (
    "EXISTS (SELECT 1 FROM slide_templates t "
    "WHERE t.template_id = template_versions.template_id "
    f"AND (t.tenant_id = {_GUC} OR t.is_system))"
)
_PARENT_OWNED = (
    "EXISTS (SELECT 1 FROM slide_templates t "
    "WHERE t.template_id = template_versions.template_id "
    f"AND t.tenant_id = {_GUC})"
)
_VERSIONS = [
    "ALTER TABLE template_versions ENABLE ROW LEVEL SECURITY",
    f"DROP POLICY IF EXISTS {POLICY_NAME} ON template_versions",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_read ON template_versions",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_ins ON template_versions",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_upd ON template_versions",
    f"DROP POLICY IF EXISTS {POLICY_NAME}_del ON template_versions",
    f"CREATE POLICY {POLICY_NAME}_read ON template_versions FOR SELECT USING ({_PARENT_VISIBLE})",
    f"CREATE POLICY {POLICY_NAME}_ins ON template_versions FOR INSERT WITH CHECK ({_PARENT_OWNED})",
    f"CREATE POLICY {POLICY_NAME}_upd ON template_versions FOR UPDATE "
    f"USING ({_PARENT_OWNED}) WITH CHECK ({_PARENT_OWNED})",
    f"CREATE POLICY {POLICY_NAME}_del ON template_versions FOR DELETE USING ({_PARENT_OWNED})",
]

_ALL_POLICIES = {
    "deck_jobs": [POLICY_NAME],
    "slide_templates": [
        POLICY_NAME,
        f"{POLICY_NAME}_read",
        f"{POLICY_NAME}_ins",
        f"{POLICY_NAME}_upd",
        f"{POLICY_NAME}_del",
    ],
    "template_versions": [
        POLICY_NAME,
        f"{POLICY_NAME}_read",
        f"{POLICY_NAME}_ins",
        f"{POLICY_NAME}_upd",
        f"{POLICY_NAME}_del",
    ],
}


def upgrade_statements() -> list[str]:
    """All DDL to enable RLS + the custom policies (migration ``upgrade()``)."""
    return [*_DECK, *_TEMPLATES, *_VERSIONS]


def downgrade_statements() -> list[str]:
    """Reverse of :func:`upgrade_statements` (fully reversible)."""
    out: list[str] = []
    for table, policies in _ALL_POLICIES.items():
        for p in policies:
            out.append(f"DROP POLICY IF EXISTS {p} ON {table}")
        out.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return out


def register_rls_policies(metadata: MetaData) -> None:
    """Apply the custom policies after create_all (tests), single-sourced with prod."""

    def _apply(target: object, connection: Connection, **kw: object) -> None:
        for stmt in upgrade_statements():
            connection.execute(text(stmt))

    event.listen(metadata, "after_create", _apply)
