"""Regression: an UNSHAPED or partially-empty deck must be visible to the CALLER.

Two silent-degradation paths converge on the same stage symptom -- a deck of
clean-looking slides with missing or unsummarised content, reported 100% green:

1. ``ContentShaper.shape_content`` fails OPEN (correctly: losing a paid deck to a
   transient LLM failure is worse than shipping it raw). But the degradation
   reached nobody. The job reported success and the ONLY evidence was an ERROR
   log line -- while the worker_management call was still billed.
2. The PPTX renderers drop unrenderable list entries. ``_add_degraded_marker``
   put that on the slide's face for exactly two templates; everywhere else a
   destroyed payload rendered as a tidy, successful, EMPTY panel.

Neither is fixed by failing the job. Both are fixed by making the loss
machine-readable on the manifest and visible on the slide.

The false-positive guards here are load-bearing and are NOT filler: a signal
that fires on a healthy deck is worse than no signal, because every consumer
learns to ignore it.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pptx import Presentation
from w2v_common.clients import WMResponse

from src.api.schemas import DeckManifestResponse
from src.config import Settings, clear_settings_cache
from src.services.content_shaper import (
    REASON_NO_USABLE_OUTPUT,
    REASON_PARTIAL_MERGE,
    REASON_PROMPT_RENDER_FAILED,
    REASON_WM_CALL_FAILED,
    SHAPING_FAILED,
    ContentShaper,
    ShapingReport,
)
from src.services.deck_builder import DeckBuilder
from src.services.template_renderer import render_slide

from .test_deck_builder_async import (
    _as_session,
    _FakeExecuteResult,
    _make_template,
    _SequentialDB,
    _stub_storage,
)


@pytest.fixture(autouse=True)
def _reset_settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def brand() -> dict:
    return {
        "primary_color": "#1a73e8",
        "secondary_color": "#34a853",
        "font_family": "Arial",
        "theme": "modern",
    }


@pytest.fixture
def styling_rules() -> dict:
    return {"title_font_size": 28, "layout": "centered"}


@pytest.fixture
def three_slides() -> list[dict]:
    return [
        {
            "template_name": "title",
            "content": {"title": f"Slide {i}", "bullets": ["one", "two"]},
            "slot_definitions": {"title": {"type": "text"}},
        }
        for i in range(3)
    ]


def _wm_returning(output: object) -> AsyncMock:
    wm = AsyncMock()
    wm.message = AsyncMock(
        return_value=WMResponse(
            output=output,
            output_type="object",
            output_schema_used=None,
            metadata=None,
            usage_metrics=None,
        )
    )
    return wm


def _texts(slide) -> list[str]:
    out = []
    for shape in slide.shapes:
        if hasattr(shape, "text_frame"):
            text = shape.text_frame.text.strip()
            if text:
                out.append(text)
    return out


def _marked(slide) -> bool:
    return any("could not be displayed" in t for t in _texts(slide))


# ---------------------------------------------------------------------------
# 1. Shaping failure reaches the caller as structured data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wm_failure_ships_raw_slides_and_reports_shaping_failed(
    settings, three_slides, brand
):
    """The load-bearing case: WM is down, the deck ships RAW, and it SAYS so.

    Fail-open is preserved -- the caller still gets its three slides, byte for
    byte -- but the report now carries the count of slides that ship
    unsummarised and with no generated speaker notes.
    """
    wm = AsyncMock()
    wm.message = AsyncMock(side_effect=RuntimeError("wm unreachable"))
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    result = await shaper.shape_content(three_slides, brand, {"enabled": True}, report=sink)

    # Fail-open intact: the raw bullets survive untouched.
    assert result == three_slides
    assert result[0]["content"]["bullets"] == ["one", "two"]
    # ...and the degradation is now machine-readable.
    assert len(sink) == 1
    assert sink[0].code == SHAPING_FAILED
    assert sink[0].reason == REASON_WM_CALL_FAILED
    assert sink[0].requested == 3
    assert sink[0].shaped == 0
    assert sink[0].unshaped == 3


@pytest.mark.asyncio
async def test_healthy_shaping_reports_an_empty_signal(settings, three_slides, brand):
    """FALSE-POSITIVE GUARD. This protects every deck that already works.

    A fully-shaped deck must report ``code == ""``. If this can fail, the signal
    fires on healthy decks and every consumer learns to ignore it -- which is
    strictly worse than the silent ERROR log it replaced.
    """
    wm = _wm_returning(
        {
            "slides": [
                {"index": i, "content": {"title": f"Polished {i}"}, "speaker_notes": "notes"}
                for i in range(3)
            ]
        }
    )
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    result = await shaper.shape_content(three_slides, brand, {"enabled": True}, report=sink)

    assert result[0]["content"] == {"title": "Polished 0"}
    assert sink[0].code == ""
    assert sink[0].reason == ""
    assert sink[0].requested == 3
    assert sink[0].shaped == 3
    assert sink[0].unshaped == 0


@pytest.mark.asyncio
async def test_shaping_disabled_reports_an_empty_signal(settings, three_slides, brand):
    """FALSE-POSITIVE GUARD. A deck that never asked for shaping is not degraded.

    ``requested == 0`` with an empty code is the "deliberately unshaped" case and
    must stay indistinguishable from a shaped one, exactly as ``image_slots_*``
    treats a deliberately text-only deck.
    """
    wm = _wm_returning({"slides": []})
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    result = await shaper.shape_content(three_slides, brand, {"enabled": False}, report=sink)

    assert result == three_slides
    wm.message.assert_not_called()
    assert sink[0] == ShapingReport()
    assert sink[0].code == ""


@pytest.mark.asyncio
async def test_partial_merge_counts_only_the_slides_that_stayed_raw(settings, three_slides, brand):
    """The model answered for 1 of 3. That is a degradation of exactly 2."""
    wm = _wm_returning({"slides": [{"index": 1, "content": {"title": "Only me"}}]})
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    result = await shaper.shape_content(three_slides, brand, {"enabled": True}, report=sink)

    assert result[1]["content"] == {"title": "Only me"}
    assert result[0]["content"]["title"] == "Slide 0"
    assert sink[0].code == SHAPING_FAILED
    assert sink[0].reason == REASON_PARTIAL_MERGE
    assert sink[0].shaped == 1
    assert sink[0].unshaped == 2


@pytest.mark.asyncio
async def test_slide_whose_every_key_was_rejected_counts_as_unshaped(settings, three_slides, brand):
    """A slide the model NAMED but delivered nothing usable for is still raw.

    Counting it as shaped would make the report agree with the LLM's claim
    rather than with what the tenant receives.
    """
    wm = _wm_returning(
        {"slides": [{"index": i, "content": "a string, not a dict"} for i in range(3)]}
    )
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    result = await shaper.shape_content(three_slides, brand, {"enabled": True}, report=sink)

    assert result[0]["content"] == {"title": "Slide 0", "bullets": ["one", "two"]}
    assert sink[0].shaped == 0
    assert sink[0].unshaped == 3
    assert sink[0].code == SHAPING_FAILED


@pytest.mark.asyncio
async def test_unusable_wm_output_reports_no_usable_output(settings, three_slides, brand):
    """WM answered, the call was billed, and nothing came back in a shape we read."""
    wm = _wm_returning({"result": [{"index": 0}]})
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    result = await shaper.shape_content(three_slides, brand, {"enabled": True}, report=sink)

    assert result == three_slides
    assert sink[0].code == SHAPING_FAILED
    assert sink[0].reason == REASON_NO_USABLE_OUTPUT
    assert sink[0].unshaped == 3


@pytest.mark.asyncio
async def test_unrenderable_prompt_reports_prompt_render_failed(settings, three_slides, brand):
    """No WM call was made at all, so nothing was billed -- but the deck is raw."""
    wm = _wm_returning({"slides": []})
    shaper = ContentShaper(settings, wm_client=wm)
    sink: list[ShapingReport] = []

    with patch("src.services.content_shaper._render_shaping_prompt", return_value=None):
        result = await shaper.shape_content(three_slides, brand, {"enabled": True}, report=sink)

    assert result == three_slides
    wm.message.assert_not_called()
    assert sink[0].code == SHAPING_FAILED
    assert sink[0].reason == REASON_PROMPT_RENDER_FAILED
    assert sink[0].unshaped == 3


# ---------------------------------------------------------------------------
# 2. The signal reaches the API response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_carries_the_shaping_signal_and_the_job_still_succeeds():
    """End to end through ``build_deck``: WM dies, the deck still ships, and the
    manifest -- which is what ``GET /api/decks/{id}/manifest`` returns -- names
    the degradation. No exception, no status change.
    """
    template = _make_template("t-1")
    db = _SequentialDB(
        [
            _FakeExecuteResult(rows=[template]),
            _FakeExecuteResult(row=SimpleNamespace(version_id="v-1")),
        ]
    )
    request_payload = {
        "title": "Unshaped Deck",
        "slides": [
            {"template_id": "t-1", "content": {"title": "raw"}, "asset_urls": {}},
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": True},
        "timing": {},
    }

    with (
        patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as pdf,
        patch("src.services.deck_builder.render_slide_png", new_callable=AsyncMock) as png,
    ):
        pdf.return_value = b"%PDF-1.7 fake"
        png.return_value = b"\x89PNG\r\n\x1a\n"
        builder = DeckBuilder(Settings(), storage_service=_stub_storage())
        builder._content_shaper = MagicMock()
        builder._content_shaper.shape_content = AsyncMock(
            side_effect=RuntimeError("must not be called this way")
        )
        # Drive the REAL shaper's failure path rather than stubbing the report:
        # a stubbed sink would prove only that dict assignment works.
        real = ContentShaper(Settings(), wm_client=AsyncMock())
        real._wm.message = AsyncMock(side_effect=RuntimeError("wm unreachable"))
        builder._content_shaper = real

        template_map, version_map = await builder.prepare_templates(
            request_payload["slides"], _as_session(db)
        )
        manifest = await builder.build_deck("job-raw", request_payload, template_map, version_map)

    assert manifest["shaping_degraded_code"] == SHAPING_FAILED
    assert manifest["shaping_degraded_reason"] == REASON_WM_CALL_FAILED
    assert manifest["shaping_slides_requested"] == 1
    assert manifest["shaping_slides_unshaped"] == 1
    assert manifest["shaping_slides_shaped"] == 0
    # The deck itself still shipped -- fail-open is intact.
    assert manifest["slide_count"] == 1

    # And the response model does not drop the keys on the way out.
    response = DeckManifestResponse(**manifest)
    assert response.shaping_degraded_code == SHAPING_FAILED
    assert response.shaping_slides_unshaped == 1


def test_response_model_defaults_are_the_healthy_values():
    """FALSE-POSITIVE GUARD at the schema layer: an older manifest row, written
    before these keys existed, must deserialise as HEALTHY, not as degraded.
    """
    response = DeckManifestResponse(deck_id="d-1", title="Legacy")
    assert response.shaping_degraded_code == ""
    assert response.shaping_degraded_reason == ""
    assert response.shaping_slides_requested == 0
    assert response.shaping_slides_unshaped == 0


# ---------------------------------------------------------------------------
# 3. The degraded marker, generalised beyond metric_summary / scorecard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layout", "content"),
    [
        ("agenda", {"title": "Agenda", "items": "Intro, Body, Close"}),
        ("timeline", {"title": "Timeline", "beats": "Q1, Q2"}),
        ("process_flow", {"title": "Process", "steps": {"a": 1}}),
        ("roadmap", {"title": "Roadmap", "phases": "H1"}),
        ("feature_spotlight", {"title": "Feature", "bullets": {"a": 1}}),
        ("two_up", {"title": "Two up", "left_bullets": "a, b", "right_bullets": ["ok"]}),
    ],
)
def test_destroyed_list_payload_is_marked_on_the_slide(layout, content, brand, styling_rules):
    """A non-list payload for a list slot used to render as a tidy empty panel.

    Worse for the sequential families: a bare string was iterated per CHARACTER
    and a dict per KEY, producing a confident diagram of nonsense. Each of these
    templates previously had NO marker -- only metric_summary and scorecard did.
    """
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type=layout,
        content=content,
        brand=brand,
        styling_rules=styling_rules,
    )
    assert _marked(slide), f"{layout} rendered a silent empty panel: {_texts(slide)}"


def test_timeline_marks_entries_that_normalised_to_nothing(brand, styling_rules, caplog):
    """The bare ``continue`` in ``_normalise_beats`` was the silent drop: two
    beats with neither label nor description vanished with no warning, no count
    and nothing on the slide.
    """
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        slide = render_slide(
            prs,
            layout_type="timeline",
            content={
                "title": "Timeline",
                "beats": [{"label": "Kickoff"}, {"marker": "02"}, {"marker": "03"}],
            },
            brand=brand,
            styling_rules=styling_rules,
        )
    texts = _texts(slide)
    assert any("Kickoff" in t for t in texts)
    assert any("2 timeline beats could not be displayed" in t for t in texts)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_two_up_marks_the_column_that_lost_its_bullets(brand, styling_rules):
    """Per-COLUMN, not per-slide: a marker on the wrong half sends a reviewer to
    the wrong place.
    """
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="two_up",
        content={
            "title": "Two up",
            "left_heading": "Kept",
            "left_bullets": ["alpha", "beta"],
            "right_heading": "Lost",
            "right_bullets": [None, "", {"note": "no text key"}],
        },
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _texts(slide)
    assert any("alpha" in t for t in texts)
    assert any("3 right bullets could not be displayed" in t for t in texts)
    assert not any("left bullets could not be displayed" in t for t in texts)


@pytest.mark.parametrize(
    ("layout", "content"),
    [
        ("agenda", {"title": "Agenda", "items": ["Intro", {"text": "Body"}]}),
        ("timeline", {"title": "Timeline", "beats": [{"label": "Q1"}, "Q2"]}),
        ("process_flow", {"title": "Process", "steps": [{"label": "Build"}, "Ship"]}),
        ("roadmap", {"title": "Roadmap", "phases": [{"name": "H1", "items": ["x"]}]}),
        ("feature_spotlight", {"title": "Feature", "bullets": ["fast", {"text": "cheap"}]}),
        ("two_up", {"title": "Two up", "left_bullets": ["a"], "right_bullets": ["b"]}),
        ("agenda", {"title": "Agenda"}),
        ("two_up", {"title": "Two up", "left_heading": "L", "right_heading": "R"}),
    ],
)
def test_healthy_and_empty_slides_carry_no_marker(layout, content, brand, styling_rules):
    """FALSE-POSITIVE GUARD for every working deck.

    Two cases in one: a well-formed payload (including the plain-string entries
    these families have always accepted -- filtering those to dicts would have
    been a silent regression), and a slot that is simply ABSENT. A deliberately
    empty agenda is legitimate and must not be branded degraded.
    """
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type=layout,
        content=content,
        brand=brand,
        styling_rules=styling_rules,
    )
    assert not _marked(slide), f"{layout} falsely marked degraded: {_texts(slide)}"
