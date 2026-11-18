"""Proves the Phase D RLS policies ISOLATE tenants across slide_service's three
tenant-scoped tables — including the non-standard cases:

* ``deck_jobs`` — strict own-only.
* ``slide_templates`` — own-or-``is_system`` reads; own-only writes. The retired
  ``tenant_id = 'system'`` sentinel is GONE: ``tenant_id`` is a native ``uuid``
  holding a REAL registered tenant on every row, platform templates included, and
  cross-tenant visibility comes solely from the ``is_system`` BOOLEAN.
* ``template_versions`` — visibility derived from the parent ``slide_templates`` row.

Connects AS a throwaway non-owner ``NOBYPASSRLS`` role (the posture the runtime
``w2v_slide_app`` role has under enforce). Needs a superuser test DB (``CREATE
ROLE`` + ``SET ROLE``); skips loudly in CI where the role lacks that privilege.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

# Real uuids: ``tenant_id`` is a native ``uuid`` column, so a string like
# "tenant-aaaa" cannot even be stored any more.
TENANT_A = "0195f48b-d000-7000-8000-0000000000aa"
TENANT_B = "0195f48b-d000-7000-8000-0000000000bb"
#: Owner of the platform templates. An ORDINARY tenant uuid — it grants nothing on
#: its own; ``is_system`` is what makes a row globally readable. Deliberately a
#: third distinct tenant so a policy that leaked by uuid instead of by flag fails.
SYSTEM_OWNER = "0195f48b-d000-7000-8000-000000000099"
_ROLE = "w2v_slide_app_rls_test"

_INS_DECK = "INSERT INTO deck_jobs (job_id, tenant_id, status) VALUES (:id, :t, 'pending')"
_INS_TMPL = (
    "INSERT INTO slide_templates "
    "(template_id, tenant_id, name, display_name, category, layout_type, "
    "slot_definitions, styling_rules, is_system, is_active) "
    "VALUES (:id, :t, :name, :name, 'cat', 'layout', '{}'::jsonb, '{}'::jsonb, :sys, true)"
)
_INS_VER = (
    "INSERT INTO template_versions "
    "(version_id, template_id, version_number, snapshot_hash, slot_definitions, styling_rules) "
    "VALUES (:id, :tmpl, 1, 'hash', '{}'::jsonb, '{}'::jsonb)"
)


@pytest.mark.asyncio
async def test_rls_isolates_tenants_under_restricted_role(db_engine) -> None:
    async with db_engine.begin() as conn:
        try:
            await conn.execute(text(f'CREATE ROLE "{_ROLE}" NOSUPERUSER NOBYPASSRLS NOINHERIT'))
        except ProgrammingError as exc:
            pytest.skip(f"cannot CREATE ROLE (needs superuser/CREATEROLE): {exc.orig}")
        for tbl in ("deck_jobs", "slide_templates", "template_versions"):
            await conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO "{_ROLE}"'))

        # Seed as owner (RLS bypassed): deck_jobs A/B; templates A/B/system; versions A/B.
        await conn.execute(text(_INS_DECK), {"id": "deck-a", "t": TENANT_A})
        await conn.execute(text(_INS_DECK), {"id": "deck-b", "t": TENANT_B})
        await conn.execute(
            text(_INS_TMPL), {"id": "tpl-a", "t": TENANT_A, "name": "a", "sys": False}
        )
        await conn.execute(
            text(_INS_TMPL), {"id": "tpl-b", "t": TENANT_B, "name": "b", "sys": False}
        )
        await conn.execute(
            text(_INS_TMPL), {"id": "tpl-sys", "t": SYSTEM_OWNER, "name": "sys", "sys": True}
        )
        # Owned by the SAME tenant as tpl-sys but is_system=False: proves
        # visibility keys on the FLAG, never on the platform tenant's uuid.
        await conn.execute(
            text(_INS_TMPL),
            {"id": "tpl-sysowner-private", "t": SYSTEM_OWNER, "name": "p", "sys": False},
        )
        await conn.execute(text(_INS_VER), {"id": "ver-a", "tmpl": "tpl-a"})
        await conn.execute(text(_INS_VER), {"id": "ver-b", "tmpl": "tpl-b"})

        try:
            await conn.execute(text(f'SET LOCAL ROLE "{_ROLE}"'))

            # No GUC → strict tables fail-closed to 0. slide_templates is
            # own-or-is_system: platform templates are intentionally global
            # (shared, non-sensitive), so with no tenant bound only the
            # ``is_system`` row is visible — and crucially NO tenant-owned row,
            # not even the private one owned by the platform tenant itself.
            # template_versions on
            # tenant-owned parents are hidden (no system-owned version seeded).
            for tbl in ("deck_jobs", "template_versions"):
                n = (await conn.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar()
                assert n == 0, f"{tbl} leaked {n} rows with no GUC"
            no_guc_tpls = set(
                (await conn.execute(text("SELECT template_id FROM slide_templates")))
                .scalars()
                .all()
            )
            assert no_guc_tpls == {"tpl-sys"}, (
                f"no-GUC slide_templates exposed non-is_system rows: {no_guc_tpls}"
            )

            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"), {"t": TENANT_A}
            )
            # deck_jobs: strict → only A.
            decks = (await conn.execute(text("SELECT job_id FROM deck_jobs"))).scalars().all()
            assert decks == ["deck-a"], decks
            # slide_templates: own-or-is_system → A's own + the is_system row.
            # NOT B's, and NOT the platform tenant's non-system row.
            tpls = set(
                (await conn.execute(text("SELECT template_id FROM slide_templates")))
                .scalars()
                .all()
            )
            assert tpls == {"tpl-a", "tpl-sys"}, tpls
            # Both halves, stated separately so a regression names itself.
            assert "tpl-sys" in tpls, "is_system template invisible to tenant A"
            assert "tpl-b" not in tpls, "tenant A can see tenant B's template"
            assert "tpl-sysowner-private" not in tpls, (
                "non-system row leaked because its owner is the platform tenant"
            )
            # template_versions: parent-derived → only A's template version.
            vers = (
                (await conn.execute(text("SELECT version_id FROM template_versions")))
                .scalars()
                .all()
            )
            assert vers == ["ver-a"], vers

            # WITH CHECK: A cannot create a deck for B.
            await conn.execute(text("SAVEPOINT s"))
            with pytest.raises(ProgrammingError):
                await conn.execute(text(_INS_DECK), {"id": "deck-x", "t": TENANT_B})
            await conn.execute(text("ROLLBACK TO SAVEPOINT s"))

            # Bind tenant B: the SAME is_system row must be visible to a second,
            # unrelated tenant — "every tenant sees is_system" is not a
            # one-tenant accident.
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"), {"t": TENANT_B}
            )
            tpls_b = set(
                (await conn.execute(text("SELECT template_id FROM slide_templates")))
                .scalars()
                .all()
            )
            assert tpls_b == {"tpl-b", "tpl-sys"}, tpls_b
        finally:
            await conn.execute(text("RESET ROLE"))
            await conn.execute(text(f'DROP OWNED BY "{_ROLE}"'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{_ROLE}"'))
