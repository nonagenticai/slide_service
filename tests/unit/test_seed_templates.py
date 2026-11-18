"""Tests for template seeding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import SlideTemplate
from src.seed.seed_templates import (
    SEED_TEMPLATES,
    SYSTEM_TEMPLATE_TENANT_ID,
    seed_templates,
)


class _Scalars:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExecuteResult:
    def __init__(self, row: object | None, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self) -> object | None:
        return self._row

    def scalar(self) -> object | None:
        return self._row

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _StubDB:
    def __init__(self, *, existing_names: set[str] | None = None) -> None:
        self._existing = existing_names or set()
        self._added: list[SlideTemplate] = []
        self._flushed = False
        self._sql: list[str] = []

    # ``params`` is required: seed_templates now issues two parameterised
    # statements before it inserts anything -- the ``app.current_tenant`` bind
    # (without which the INSERT is rejected by RLS in prod) and the advisory
    # lock that stops two replicas double-seeding.
    async def execute(self, stmt: object, params: object | None = None) -> _ExecuteResult:
        self._sql.append(str(stmt))
        # Check if the statement is looking for a template name
        # For simplicity, always return None (template doesn't exist) if not in existing set
        for name in self._existing:
            # If any existing name matches, return a fake template
            return _ExecuteResult(SimpleNamespace(name=name))
        return _ExecuteResult(None)

    def add(self, obj: object) -> None:
        self._added.append(cast(SlideTemplate, obj))

    async def flush(self) -> None:
        self._flushed = True

    def expire_all(self) -> None:
        """No-op: this stub never round-trips through a database.

        ``seed_templates`` ends by calling ``freeze_system_template_versions``,
        which expires and re-reads the system templates. This stub answers that
        re-read with NO rows (``scalars().all() == []``), so the freeze is a
        no-op here and these tests keep measuring only what they are about:
        which templates the seed inserts and updates. The freeze itself cannot
        be proven against a stub — it is about what an RLS policy permits — and
        is covered against a real database, under a NOBYPASSRLS role, in
        ``tests/test_seed_rls_enforce.py``.
        """


@pytest.mark.asyncio
async def test_seed_templates_on_empty_db():
    """Seeding on empty DB creates all system templates."""
    db = _StubDB()
    count = await seed_templates(cast(AsyncSession, db))
    assert count == len(SEED_TEMPLATES)
    assert len(db._added) == len(SEED_TEMPLATES)
    assert db._flushed is True
    # The seed must bind the platform tenant uuid into app.current_tenant and
    # take the advisory lock BEFORE any insert -- see test_seed_rls_enforce.py
    # for the live-RLS proof that the bind is what makes the INSERT legal.
    assert any("app.current_tenant" in q for q in db._sql), db._sql
    assert any("pg_advisory_xact_lock" in q for q in db._sql), db._sql
    # Platform ownership is the is_system FLAG on a REAL tenant uuid. The
    # retired ``tenant_id = 'system'`` sentinel is not storable in a uuid column,
    # so a regression to it would be a production-boot failure, not a test-only
    # cosmetic. Assert both halves on every seeded row.
    assert all(t.is_system is True for t in db._added), [t.name for t in db._added]
    assert {t.tenant_id for t in db._added} == {SYSTEM_TEMPLATE_TENANT_ID}, {
        t.tenant_id for t in db._added
    }


def test_system_template_tenant_id_is_a_real_uuid():
    """``SYSTEM_TEMPLATE_TENANT_ID`` must parse as a uuid: ``slide_templates.tenant_id``
    is a native ``uuid`` column, so any sentinel string would fail at INSERT time.
    """
    assert str(UUID(SYSTEM_TEMPLATE_TENANT_ID)) == SYSTEM_TEMPLATE_TENANT_ID


@pytest.mark.asyncio
async def test_seed_templates_honours_explicit_tenant():
    """The owning tenant is a parameter, not a hardcoded sentinel."""
    other = "0195f48b-d000-7000-8000-0000000000cc"
    db = _StubDB()
    await seed_templates(cast(AsyncSession, db), tenant_id=other)
    assert {t.tenant_id for t in db._added} == {other}
    assert all(t.is_system is True for t in db._added)


@pytest.mark.asyncio
async def test_seed_templates_with_existing():
    """Seeding skips already-existing templates."""
    db = _StubDB(existing_names={"title"})
    count = await seed_templates(cast(AsyncSession, db))
    # Since our stub always returns a row for any query when existing_names is set,
    # all templates are treated as existing
    assert count == 0


def test_seed_template_count():
    """Verify all 16 seed templates are defined."""
    assert len(SEED_TEMPLATES) == 16


def test_seed_template_names():
    """Verify all expected template names are present."""
    names = {t["name"] for t in SEED_TEMPLATES}
    expected = {
        "title",
        "agenda",
        "scorecard",
        "finding_detail",
        "comparison",
        "feature_spotlight",
        "process_flow",
        "metric_summary",
        "roadmap",
        "cta",
        "tutorial_step",
        # SLIDE-2 layout families
        "quote",
        "stat",
        "section_divider",
        "timeline",
        "two_up",
    }
    assert names == expected


def test_seed_template_structure():
    """Verify each template has required fields."""
    required_fields = {
        "name",
        "display_name",
        "category",
        "layout_type",
        "slot_definitions",
        "styling_rules",
    }
    for template in SEED_TEMPLATES:
        for field in required_fields:
            assert field in template, f"Template {template['name']} missing field {field}"
