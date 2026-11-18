"""Proves the system-template seed WRITES under RLS ``enforce`` as a NON-OWNER role.

This is the regression guard for the 2026-08-19 production defect: both
slide-service replicas started with

    (psycopg.errors.InsufficientPrivilege) new row violates row-level security
    policy for table "slide_templates"

because ``seed_templates`` INSERTs platform-owned rows while the
``tenant_isolation_ins`` policy checks ``tenant_id = app.current_tenant``, and at
startup that GUC is unbound (NULL). ``on_startup`` swallowed it, so both pods
went Ready with 5 of 16 system templates missing.

A test that runs as the table OWNER proves nothing here — owner bypass under
ENABLE-not-FORCE makes the INSERT succeed no matter what the policy says. So
this connects AS a throwaway ``NOSUPERUSER NOBYPASSRLS`` role (the posture the
runtime ``w2v_slide_app`` role has) exactly like ``tests/test_rls_isolation.py``,
and additionally asserts a CONTROL insert (a non-sentinel tenant, GUC bound to
the seed's own tenant) is REJECTED. If the policies were absent or the role were bypassing
them, that control would succeed and this test fails — so a green run cannot mean
"RLS was off".
"""

from __future__ import annotations

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Settings
from src.database.models import SlideTemplate
from src.seed.seed_templates import (
    SYSTEM_TEMPLATE_NAMES,
    SYSTEM_TEMPLATE_TENANT_ID,
    freeze_system_template_versions,
    missing_system_templates,
    seed_templates,
)
from src.services.deck_builder import DeckBuilder
from src.services.template_snapshot import snapshot_hash_for

_ROLE = "w2v_slide_app_seed_test"

_INS_OTHER_TENANT = (
    "INSERT INTO slide_templates "
    "(template_id, tenant_id, name, display_name, category, layout_type, "
    "slot_definitions, styling_rules, is_system, is_active) "
    "VALUES ('ctl-1', '0195f48b-d000-7000-8000-000000000101', 'ctl', 'ctl', 'cat', 'layout', "
    "'{}'::jsonb, '{}'::jsonb, false, true)"
)


async def _drop_role(conn) -> None:
    """Remove the throwaway role and its grants.

    Explicit, narrowly-scoped REVOKEs rather than ``DROP OWNED BY`` / ``REVOKE …
    ON ALL SEQUENCES``: on postgres 18 both error with ``permission denied for
    column "tableoid" of relation "template_versions_id_seq"``, which would fail
    the test in teardown. Only the two objects the fixture granted are touched.
    """
    exists = (
        await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ROLE})
    ).scalar()
    if not exists:
        return
    for seq in ("slide_templates_id_seq", "deck_jobs_id_seq", "template_versions_id_seq"):
        await conn.execute(text(f'REVOKE ALL ON SEQUENCE {seq} FROM "{_ROLE}"'))
    for tbl in ("slide_templates", "deck_jobs", "template_versions"):
        await conn.execute(text(f'REVOKE ALL ON TABLE {tbl} FROM "{_ROLE}"'))
    await conn.execute(text(f'DROP ROLE IF EXISTS "{_ROLE}"'))


async def _reset_role(session) -> None:
    """Return the pooled connection to the owner role, durably.

    ``RESET ROLE`` issued after a rollback opens a NEW implicit transaction; if
    the session then closes without committing, the reset is rolled back too and
    the connection goes back to the pool still impersonating the restricted role
    — which then fails the fixture teardown with a confusing
    ``permission denied for column "tableoid"``. Commit it.
    """
    await session.rollback()
    await session.execute(text("RESET ROLE"))
    await session.commit()


@pytest.fixture
async def restricted_role(db_engine):
    """Create/drop a NOBYPASSRLS role with the runtime's grants on slide_templates."""
    async with db_engine.begin() as conn:
        # Drop first: a role left behind by a crashed run would make CREATE ROLE
        # raise DuplicateObject, and the skip below would then silently turn this
        # regression guard into a no-op ("skipped" reads green).
        await _drop_role(conn)
        try:
            await conn.execute(text(f'CREATE ROLE "{_ROLE}" NOSUPERUSER NOBYPASSRLS NOINHERIT'))
        except InsufficientPrivilege as exc:
            pytest.skip(f"cannot CREATE ROLE (needs superuser/CREATEROLE): {exc}")
        for tbl in ("slide_templates", "template_versions"):
            await conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO "{_ROLE}"'))
        # Both ``id`` columns are SERIAL — without sequence USAGE the INSERT fails
        # with a permission error that has nothing to do with RLS. The runtime
        # ``w2v_slide_app`` role holds both grants (the request path has always
        # written ``template_versions``), so this mirrors production, it does not
        # relax the posture under test: the role is still NOBYPASSRLS.
        for seq in ("slide_templates_id_seq", "template_versions_id_seq"):
            await conn.execute(text(f'GRANT USAGE ON SEQUENCE {seq} TO "{_ROLE}"'))
    yield _ROLE
    async with db_engine.begin() as conn:
        await _drop_role(conn)


@pytest.mark.asyncio
async def test_seed_inserts_system_templates_as_non_owner_under_rls_enforce(
    db_engine, restricted_role, monkeypatch
) -> None:
    """seed_templates must insert all 16 system rows while RLS is ENFORCED on it."""
    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as session:
        await session.execute(text(f'SET ROLE "{restricted_role}"'))

        # CONTROL: with the seed's own GUC (the platform-owner tenant uuid)
        # bound, a write for a
        # DIFFERENT tenant must be rejected. This is what proves the policies are
        # live for this role — if RLS were off, this INSERT would succeed.
        # The SAVEPOINT encloses the set_config so the rollback UNBINDS the GUC
        # again -- otherwise the control block would leave the seed's own
        # precondition already satisfied and mask the bug under test.
        await session.execute(text("SAVEPOINT ctl"))
        await session.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(SYSTEM_TEMPLATE_TENANT_ID)},
        )
        with pytest.raises(ProgrammingError):
            await session.execute(text(_INS_OTHER_TENANT))
        await session.execute(text("ROLLBACK TO SAVEPOINT ctl"))
        assert (
            await session.execute(text("SELECT current_setting('app.current_tenant', true)"))
        ).scalar() in (None, ""), "control block leaked a bound GUC into the seed"

        # The real thing. Before the fix this raises InsufficientPrivilege.
        seeded = await seed_templates(session)
        assert seeded == len(SYSTEM_TEMPLATE_NAMES), seeded
        assert await missing_system_templates(session) == set()

        rows = await session.execute(
            text("SELECT name, tenant_id FROM slide_templates WHERE is_system")
        )
        seen = {row[0]: row[1] for row in rows.all()}
        assert set(seen) == set(SYSTEM_TEMPLATE_NAMES), sorted(seen)
        # The column is a native ``uuid``, so psycopg hands back ``uuid.UUID``
        # while the seeder's constant is a string. Compare on a single
        # representation rather than letting the type difference read as a
        # seeding failure.
        assert {str(v) for v in seen.values()} == {str(SYSTEM_TEMPLATE_TENANT_ID)}, seen

        await _reset_role(session)


@pytest.mark.asyncio
async def test_seed_is_idempotent_as_non_owner_under_rls_enforce(
    db_engine, restricted_role, monkeypatch
) -> None:
    """A second seed in a fresh transaction inserts nothing (no duplicate rows)."""
    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as session:
        await session.execute(text(f'SET ROLE "{restricted_role}"'))
        assert await seed_templates(session) == len(SYSTEM_TEMPLATE_NAMES)
        await session.commit()

        assert await seed_templates(session) == 0
        total = (
            await session.execute(text("SELECT count(*) FROM slide_templates WHERE is_system"))
        ).scalar()
        assert total == len(SYSTEM_TEMPLATE_NAMES), total

        await _reset_role(session)


# A tenant that is NOT the platform-template owner — the posture every real
# caller of the deck API is in.
_RENDERING_TENANT = "0195f48b-d000-7000-8000-000000000202"


async def _bind(session, tenant_id: str) -> None:
    """Bind ``app.current_tenant`` for the current transaction."""
    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id}
    )


async def _system_templates(session) -> list[tuple[str, str]]:
    """``(template_id, name)`` for every seeded system template."""
    rows = await session.execute(
        text("SELECT template_id, name FROM slide_templates WHERE is_system ORDER BY name")
    )
    return [(r[0], r[1]) for r in rows.all()]


async def _version_count(session) -> int:
    return (await session.execute(text("SELECT count(*) FROM template_versions"))).scalar()


@pytest.mark.asyncio
async def test_seed_freezes_a_version_matching_every_system_templates_snapshot(
    db_engine, restricted_role, monkeypatch
) -> None:
    """Every seeded system template ends up with a version at its CURRENT hash.

    This is the precondition the request path depends on. 13 of the 16 templates
    in production had ZERO frozen versions and ``title`` had only a STALE one,
    which is what made every deck containing them fail.
    """
    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as session:
        await session.execute(text(f'SET ROLE "{restricted_role}"'))
        await seed_templates(session)

        templates = (
            (await session.execute(select(SlideTemplate).where(SlideTemplate.is_system.is_(True))))
            .scalars()
            .all()
        )
        assert len(templates) == len(SYSTEM_TEMPLATE_NAMES)

        for template in templates:
            wanted = snapshot_hash_for(template)
            found = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM template_versions "
                        "WHERE template_id = :tid AND snapshot_hash = :h"
                    ),
                    {"tid": template.template_id, "h": wanted},
                )
            ).scalar()
            assert found == 1, f"{template.name} has no version at its current snapshot"

        await _reset_role(session)


@pytest.mark.asyncio
async def test_freeze_is_idempotent(db_engine, restricted_role, monkeypatch) -> None:
    """A second freeze over unchanged templates writes nothing."""
    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as session:
        await session.execute(text(f'SET ROLE "{restricted_role}"'))
        await seed_templates(session)
        await session.commit()

        await _bind(session, str(SYSTEM_TEMPLATE_TENANT_ID))
        before = await _version_count(session)
        assert await freeze_system_template_versions(session) == 0
        assert await _version_count(session) == before

        await _reset_role(session)


@pytest.mark.asyncio
async def test_a_tenant_renders_every_system_template_without_writing_a_version(
    db_engine, restricted_role, monkeypatch
) -> None:
    """THE regression guard: the request path must never need to INSERT a version.

    Reproduces the production failure end to end. ``DeckBuilder.prepare_templates``
    is the real request-path entry point; under a NON-owner tenant it may read a
    platform template but ``tenant_isolation_ins`` forbids it from freezing one.
    Before the seed-side freeze this raised

        (psycopg.errors.InsufficientPrivilege) new row violates row-level
        security policy for table "template_versions"

    for every system template with a stale or absent version, failing the whole
    deck job. Asserting the version count is UNCHANGED (not merely that no
    exception escaped) is what pins the fix to the right mechanism: the request
    path finds the seed's row, rather than quietly succeeding because the caller
    turned out to own something.
    """
    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as session:
        await session.execute(text(f'SET ROLE "{restricted_role}"'))
        await seed_templates(session)
        await session.commit()

        templates = await _system_templates(session)
        await session.commit()

        # Now act as an ordinary tenant, exactly like a deck request does.
        await _bind(session, _RENDERING_TENANT)
        before = await _version_count(session)

        slides_data = [{"template_id": tid} for tid, _ in templates]
        _, version_map = await DeckBuilder(Settings()).prepare_templates(slides_data, session)

        assert set(version_map) == {tid for tid, _ in templates}
        assert await _version_count(session) == before, (
            "the request path wrote a template_versions row; the seed freeze did not cover it"
        )

        await _reset_role(session)


@pytest.mark.asyncio
async def test_an_unfrozen_system_template_is_still_refused_to_a_tenant(
    db_engine, restricted_role, monkeypatch
) -> None:
    """Negative control — proves the guard above is not vacuous.

    If ``tenant_isolation_ins`` were absent, or this role were bypassing RLS, the
    test above would pass no matter what the seed did. So: delete the frozen
    versions (as their owner) and confirm the very same request-path call now
    RAISES. That is the production defect reproduced deliberately, and it is what
    demonstrates the passing guard is carried by the seed-side freeze rather than
    by an inert policy.
    """
    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as session:
        await session.execute(text(f'SET ROLE "{restricted_role}"'))
        await seed_templates(session)
        await session.commit()

        templates = await _system_templates(session)

        # Owner tenant may delete its own templates' versions.
        await _bind(session, str(SYSTEM_TEMPLATE_TENANT_ID))
        await session.execute(text("DELETE FROM template_versions"))
        await session.commit()
        assert await _version_count(session) == 0

        await _bind(session, _RENDERING_TENANT)
        slides_data = [{"template_id": templates[0][0]}]
        with pytest.raises(ProgrammingError) as excinfo:
            await DeckBuilder(Settings()).prepare_templates(slides_data, session)
        assert isinstance(excinfo.value.orig, InsufficientPrivilege), excinfo.value

        await _reset_role(session)
