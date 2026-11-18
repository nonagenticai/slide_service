"""Real-DB regression test for deck-generation transaction scoping.

Guards the fix for the failure mode where the deck background job held a DB
connection idle-in-transaction across the long generation pipeline (LLM content
shaping + PPTX/PDF render + storage I/O): the backend could be reaped and the
final write would then hit a dead socket. The fix runs the DB phase (resolve
templates + freeze versions) in a short session that commits and releases the
connection *before* generation; ``build_deck`` then runs holding no connection.

This is the slide_service analog of analysis_service's #52 regression test.
Because ``_process_deck`` owns its sessions internally (it does not receive one),
"the connection is not held during generation" is asserted as: by the time
``build_deck`` runs, the version-freeze write is already committed and visible
from an *independent* session. Two properties:

1. Freeze write is committed + visible from an independent session during
   generation (connection released, not held idle-in-transaction).
2. The final job status is committed — visible from an independent session.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.api.routes import _process_deck
from src.config import Settings
from src.database.models import DeckJob, SlideTemplate, TemplateVersion
from src.services.deck_builder import DeckBuilder

#: ``slide_templates.tenant_id`` / ``deck_jobs.tenant_id`` are native ``uuid``
#: columns; the old ``"t1"`` literal is no longer a legal value.
TENANT_T1 = "0195f48b-d000-7000-8000-000000000001"


def _seed_template_kwargs(template_id: str) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "tenant_id": TENANT_T1,
        "name": "title",
        "display_name": "Title",
        "category": "title",
        "layout_type": "title",
        "slot_definitions": {"title": {"type": "text"}},
        "styling_rules": {"layout": "centered"},
        "default_transition": {"type": "fade", "duration_seconds": 0.5},
        "default_timing_hint_seconds": 10.0,
    }


@pytest.mark.asyncio
async def test_process_deck_releases_connection_before_generation(db_session_factory):
    """The DB phase must commit + release before the generation phase runs."""
    template_id = str(uuid4())
    job_id = str(uuid4())

    # Seed template + processing job (committed) via independent sessions.
    async with db_session_factory() as seed:
        seed.add(SlideTemplate(**_seed_template_kwargs(template_id)))
        seed.add(DeckJob(job_id=job_id, tenant_id=TENANT_T1, status="processing"))
        await seed.commit()

    # Route get_db_session() (used by prepare_templates + the status write) to the
    # test factory so its writes land in the same DB the verification reads.
    @asynccontextmanager
    async def _test_get_db_session():
        async with db_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    freeze_visible_during_gen: list[int] = []

    async def fake_build_deck(
        _job_id: str,
        _payload: dict[str, Any],
        _template_map: dict[str, Any],
        _version_map: dict[str, str],
    ) -> dict[str, Any]:
        # Generation phase: prepare_templates must have committed + released the
        # connection, so the freeze rows are visible from an independent session.
        async with db_session_factory() as probe:
            rows = (await probe.execute(select(TemplateVersion))).scalars().all()
        freeze_visible_during_gen.append(len(rows))
        return {"slide_count": 1}

    builder = DeckBuilder(Settings(), storage_service=MagicMock())

    request_payload = {
        "title": "Test Deck",
        "slides": [{"template_id": template_id, "content": {"title": "Hi"}}],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }

    # Real prepare_templates (exercises DB resolve + freeze); stub the generation phase.
    with (
        patch("src.api.routes.get_db_session", _test_get_db_session),
        patch.object(builder, "build_deck", AsyncMock(side_effect=fake_build_deck)),
    ):
        await _process_deck(builder, job_id, request_payload, SimpleNamespace(), None, TENANT_T1)

    # 1. Freeze write committed + visible from an independent session DURING generation.
    assert freeze_visible_during_gen == [1], (
        "version-freeze write was not committed/released before generation — the "
        "connection was held idle-in-transaction across the long pipeline"
    )

    # 2. Final status committed + visible from an independent session.
    async with db_session_factory() as verify:
        job = (await verify.execute(select(DeckJob).where(DeckJob.job_id == job_id))).scalar_one()
    assert job.status == "completed"
    assert job.slide_count == 1


@pytest.mark.asyncio
async def test_completion_write_failure_does_not_mark_artifacted_deck_failed(db_session_factory):
    """Regression: build_deck already wrote pptx/pdf/manifest to storage, so a
    transient error on the *completion* status write must NOT downgrade the deck to
    "failed" (it would be a downloadable deck reported as failed). The completion
    write lives OUTSIDE the build try; a failure there leaves the job non-terminal
    ("processing", recoverable) — never "failed"."""
    template_id = str(uuid4())
    job_id = str(uuid4())

    async with db_session_factory() as seed:
        seed.add(SlideTemplate(**_seed_template_kwargs(template_id)))
        seed.add(DeckJob(job_id=job_id, tenant_id=TENANT_T1, status="processing"))
        await seed.commit()

    # get_db_session call ordering in _process_deck (build_deck stubbed):
    #   #1 = prepare_templates DB phase, #2 = the completion status write.
    # Fail #2 at commit time to simulate a transient DB error AFTER artifacts stored.
    call_index = 0

    @asynccontextmanager
    async def _flaky_get_db_session():
        nonlocal call_index
        call_index += 1
        this_call = call_index
        async with db_session_factory() as session:
            try:
                yield session
                if this_call == 2:
                    raise OSError("transient failure committing 'completed'")
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def fake_build_deck(
        _job_id: str,
        _payload: dict[str, Any],
        _template_map: dict[str, Any],
        _version_map: dict[str, str],
    ) -> dict[str, Any]:
        # Artifacts are "written to storage" here; the deck exists + is downloadable.
        return {"slide_count": 1}

    builder = DeckBuilder(Settings(), storage_service=MagicMock())
    request_payload = {
        "title": "Test Deck",
        "slides": [{"template_id": template_id, "content": {"title": "Hi"}}],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }

    with (
        patch("src.api.routes.get_db_session", _flaky_get_db_session),
        patch.object(builder, "build_deck", AsyncMock(side_effect=fake_build_deck)),
    ):
        # Must not raise: the completion-write failure is caught + logged, not fatal.
        await _process_deck(builder, job_id, request_payload, SimpleNamespace(), None, TENANT_T1)

    async with db_session_factory() as verify:
        job = (await verify.execute(select(DeckJob).where(DeckJob.job_id == job_id))).scalar_one()
    # The artifacted deck must NOT be marked failed by a transient completion error.
    assert job.status != "failed", "a stored/downloadable deck was downgraded to 'failed'"
    # Left recoverable (non-terminal), not falsely terminal.
    assert job.status == "processing"
