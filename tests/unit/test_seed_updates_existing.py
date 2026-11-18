"""Editing a SEED_TEMPLATES entry must reach already-seeded environments.

``seed_templates`` did ``if existing: continue`` with no upsert, so every edit to
a system template's slot schema, styling rules, transition or timing hint was
INERT everywhere the seed had ever run — while ``/health`` happily reported the
seed as OK because all 16 names were present. (Seeding itself does run: the
``is_system`` row count went 11 -> 16 on an earlier deploy. Only UPDATES were
lost.)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.seed.seed_templates import SEED_TEMPLATES, seed_templates

_TITLE = next(t for t in SEED_TEMPLATES if t["name"] == "title")


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
    """Returns ``existing`` for the row named ``existing.name``, None otherwise."""

    def __init__(self, existing: Any) -> None:
        self._existing = existing
        self.added: list[object] = []
        self.flushed = False

    async def execute(self, stmt: object, params: object | None = None) -> _ExecuteResult:
        sql = str(stmt)
        if "SELECT" not in sql.upper() or "slide_templates" not in sql:
            return _ExecuteResult(None)  # the GUC bind + advisory lock
        # The closing ``freeze_system_template_versions`` re-reads ALL system
        # templates -- no name bind. Answer it with no rows so the freeze is a
        # no-op and does NOT consume a slot in the call-order matching below,
        # which would walk ``_seen`` off the end of SEED_TEMPLATES. What the
        # freeze does is an RLS question a stub cannot answer; it is proven
        # against a real database in ``tests/test_seed_rls_enforce.py``.
        if "slide_templates.name = " not in sql:
            return _ExecuteResult(None, rows=[])
        # SQLAlchemy binds the name as a parameter, so match on call order
        # instead: the first slide_templates SELECT is for SEED_TEMPLATES[0].
        self._seen = getattr(self, "_seen", -1) + 1
        name = str(SEED_TEMPLATES[self._seen]["name"])
        return _ExecuteResult(self._existing if name == self._existing.name else None)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True

    def expire_all(self) -> None:
        """No-op: this stub never round-trips through a database."""


def _stale_title_row() -> SimpleNamespace:
    return SimpleNamespace(
        name="title",
        display_name="OLD Title Slide",
        category="stale-category",
        layout_type="stale_layout",
        slot_definitions={"title": {"type": "text", "max_length": 10}},
        styling_rules={"title_font_size": 8},
        default_transition={"type": "cut", "duration_seconds": 0.0},
        default_timing_hint_seconds=1.0,
        is_active=False,
    )


@pytest.mark.asyncio
async def test_seed_updates_a_drifted_existing_system_template() -> None:
    """An existing row is brought back in line with SEED_TEMPLATES, not skipped."""
    row = _stale_title_row()
    db = _StubDB(row)

    await seed_templates(cast(AsyncSession, db))

    assert row.display_name == _TITLE["display_name"]
    assert row.category == _TITLE["category"]
    assert row.layout_type == _TITLE["layout_type"]
    assert row.slot_definitions == _TITLE["slot_definitions"]
    assert row.styling_rules == _TITLE["styling_rules"]
    assert row.default_transition == _TITLE["default_transition"]
    assert row.default_timing_hint_seconds == _TITLE["default_timing_hint_seconds"]
    # A system template must not stay deactivated by drift.
    assert row.is_active is True


@pytest.mark.asyncio
async def test_seed_returns_inserted_count_not_updated_count() -> None:
    """The return value stays 'inserted', so /health and the RLS suite are unchanged."""
    db = _StubDB(_stale_title_row())
    inserted = await seed_templates(cast(AsyncSession, db))
    assert inserted == len(SEED_TEMPLATES) - 1


@pytest.mark.asyncio
async def test_seed_leaves_an_in_sync_row_untouched() -> None:
    """An identical row is not rewritten (no needless UPDATE churn every startup)."""
    row = SimpleNamespace(
        name="title",
        display_name=_TITLE["display_name"],
        category=_TITLE["category"],
        layout_type=_TITLE["layout_type"],
        slot_definitions=_TITLE["slot_definitions"],
        styling_rules=_TITLE["styling_rules"],
        default_transition=_TITLE["default_transition"],
        default_timing_hint_seconds=_TITLE["default_timing_hint_seconds"],
        is_active=True,
    )
    before = dict(vars(row))
    db = _StubDB(row)

    await seed_templates(cast(AsyncSession, db))

    assert dict(vars(row)) == before
