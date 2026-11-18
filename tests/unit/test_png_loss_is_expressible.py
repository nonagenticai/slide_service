"""The PNG path must say what it dropped — and the image guard must reach every family.

Two escapes from the fix that ``templates/base.py`` already articulated:

    "Without this a fully-malformed payload renders as a perfect empty panel: no
    exception, no missing slide, nothing for a reviewer or a downstream consumer
    to catch."

1. ``build_slide_html`` normalised every list slot (metrics / bullets / items /
   beats / column bullets) and threw the rejects away with no count and no
   marker. The PPTX path honours the principle (``_collect_dict_items`` ->
   ``_add_degraded_marker``); the PNG path did not — and the PNG is what the
   video pipeline composites, so the loss reached the finished video while the
   job read ``completed``.

2. ``resolve_slide_images`` returned EARLY for ``comparison`` and
   ``image_focus``, so ``_unrenderable_slot_report`` — written precisely to
   count image-token slots a family cannot draw — never ran on the only two
   families that HAVE images. ``deck_builder._apply_slide_asset_contract``
   resolves any ``IMAGE_SLOT_TOKENS`` key plus every free-form ``asset_urls``
   key onto ANY layout, so a ``logo_url`` on a comparison slide was written into
   ``content``, drawn nowhere, and counted ``requested=0`` — which
   ``_guard_image_slots`` reads as "deliberate text deck" and cannot fire on.

The regression that matters most is the last section: a genuinely text-only,
genuinely healthy deck must keep reporting zero of everything.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.services.deck_builder import DeckBuilder
from src.services.html_slide_renderer import (
    SlideContentReport,
    build_slide_html,
    resolve_slide_images,
)

_BRAND: dict[str, Any] = {"primary_color": "#0B1F3A"}
_GOOD_REF = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
_BAD_REF = "http://[::1"  # urlparse rejects it => load_image_bytes returns None
_MARKER = '<div class="degraded-marker">'


def _html(layout: str, content: dict[str, Any]) -> tuple[str, SlideContentReport]:
    sink: list[SlideContentReport] = []
    html = build_slide_html(layout_type=layout, content=content, brand=_BRAND, content_report=sink)
    assert len(sink) == 1, "exactly one report per render, like the image sink"
    return html, sink[0]


# ---------------------------------------------------------------------------
# Finding 1 — the PNG must say what it dropped
# ---------------------------------------------------------------------------


def test_scorecard_with_every_metric_malformed_says_so_on_the_png() -> None:
    """THE case: a payload destroyed end to end used to render a clean title frame."""
    html, report = _html("scorecard", {"title": "Q3", "metrics": ["revenue", "churn", None]})

    assert (report.offered, report.dropped) == (3, 3)
    assert report.fields == ("metrics",)
    # The count reaches the artifact the video pipeline composites, not just a log.
    assert _MARKER in html
    assert "3 metrics could not be displayed (malformed input)" in html


def test_scorecard_non_list_metrics_counts_as_one_loss() -> None:
    """``{"a": 1}`` in place of the list: one whole slot lost, not zero."""
    _html_out, report = _html("scorecard", {"title": "Q3", "metrics": {"revenue": 1}})
    assert (report.offered, report.dropped) == (1, 1)


def test_scorecard_partial_loss_still_renders_the_good_metrics() -> None:
    """A mixed payload ships what it has AND declares what it lost."""
    html, report = _html(
        "scorecard", {"title": "Q3", "metrics": [{"label": "MRR", "value": "9"}, "churn"]}
    )
    assert (report.offered, report.dropped) == (2, 1)
    assert "MRR" in html
    assert "1 metrics could not be displayed" in html


def test_timeline_with_every_beat_malformed_says_so() -> None:
    """``timeline.html.j2`` simply omits the rail on empty beats — silence by design."""
    html, report = _html("roadmap", {"title": "Plan", "phases": [{}, {"name": ""}]})
    assert (report.offered, report.dropped) == (2, 2)
    assert "2 beats could not be displayed (malformed input)" in html


def test_image_focus_bullets_loss_is_reported() -> None:
    html, report = _html(
        "finding_detail", {"title": "Finding", "bullets": [{"note": "wrong key"}, "  "]}
    )
    assert (report.offered, report.dropped) == (2, 2)
    assert "2 bullets could not be displayed" in html


def test_section_divider_present_but_malformed_slot_does_not_fall_through() -> None:
    """A present-but-destroyed ``items`` must report, not silently try ``bullets``."""
    _html_out, report = _html("agenda", {"title": "Agenda", "items": [None, ""]})
    assert (report.offered, report.dropped) == (2, 2)


def test_an_empty_first_slot_still_falls_through_to_the_next() -> None:
    """Counting must not change WHICH slot renders.

    The families with several accepted keys used a chained ``a or b or c``, so
    ``items: []`` beside ``bullets: [...]`` rendered the bullets. Deciding
    "present" with ``is not None`` instead would have silently swallowed them —
    a loss introduced by the very change meant to make loss visible.
    """
    html, report = _html("agenda", {"title": "A", "items": [], "bullets": ["keep me"]})
    assert "keep me" in html
    assert (report.offered, report.dropped) == (1, 0)
    assert _MARKER not in html


def test_two_up_counts_both_columns() -> None:
    _html_out, report = _html(
        "two_up",
        {
            "title": "Split",
            "left": {"heading": "A", "bullets": ["ok"]},
            "right": {"heading": "B", "bullets": [None, None]},
        },
    )
    assert (report.offered, report.dropped) == (3, 2)
    assert report.fields == ("right-column bullets",)


# ---------------------------------------------------------------------------
# Finding 2 — the imageless guard must reach the families that HAVE images
# ---------------------------------------------------------------------------


def test_comparison_with_an_extra_unrenderable_asset_key_is_counted() -> None:
    """``asset_urls: {"logo_url": ...}`` lands in ``content`` and is drawn nowhere."""
    _images, report = resolve_slide_images(
        "comparison",
        {
            "before_image_url": _GOOD_REF,
            "after_image_url": _GOOD_REF,
            "logo_url": _GOOD_REF,
        },
    )
    # 2 drawn slots, both loaded; the logo nobody can draw is UNDRAWN, not failed.
    # It used to be counted (3, 1) with failed_refs=(_GOOD_REF,) — a "failed
    # load" recorded against a data URI that loads fine and was never fetched.
    assert (report.requested, report.failed, report.failed_refs) == (2, 0, ())
    assert (report.undrawn, report.undrawn_slots) == (1, ("logo_url",))


def test_image_focus_second_ref_is_undrawn_not_lost() -> None:
    """Only the FIRST of screenshot/image/preview is drawn; the rest are UNDRAWN.

    The second ref is never fetched, so it is not a loss — it is a payload the
    layout cannot show. Was ``(requested, failed) == (2, 1)``.
    """
    _images, report = resolve_slide_images(
        "finding_detail", {"screenshot_url": _GOOD_REF, "preview_image_url": _GOOD_REF}
    )
    assert (report.requested, report.failed) == (1, 0)
    assert (report.undrawn, report.undrawn_slots) == (1, ("preview_image_url",))


def test_a_comparison_slide_with_only_an_undrawn_slot_is_not_vetoed() -> None:
    """End of the chain, INVERTED: an undrawn slot alone must not raise.

    ``comparison`` draws before/after; a stray ``logo_url`` is drawn by nothing
    and fetched by nothing, so "every requested image failed to load" would be
    false. Control below: the veto still fires on a real dead ref.
    """
    _images, report = resolve_slide_images("comparison", {"logo_url": _GOOD_REF})
    builder = DeckBuilder(Settings())

    builder._guard_image_slots("job-1", report)  # must not raise

    assert (report.requested, report.failed, report.undrawn) == (0, 0, 1)


def test_a_comparison_slide_with_both_refs_dead_still_trips_the_guard() -> None:
    """CONTROL: the veto is narrowed, not disabled — both DRAWN slots really died."""
    _images, report = resolve_slide_images(
        "comparison", {"before_image_url": _BAD_REF, "after_image_url": _BAD_REF}
    )
    builder = DeckBuilder(Settings())

    assert (report.requested, report.failed) == (2, 2)
    with pytest.raises(ValueError, match="every requested image failed to load"):
        builder._guard_image_slots("job-1", report)


# ---------------------------------------------------------------------------
# Regression guards — the false positive would be worse than the bug
# ---------------------------------------------------------------------------


def test_healthy_comparison_is_not_degraded() -> None:
    """Both slots drawn, nothing stray: two requested, none failed."""
    _images, report = resolve_slide_images(
        "comparison", {"before_image_url": _GOOD_REF, "after_image_url": _GOOD_REF}
    )
    assert (report.requested, report.failed) == (2, 0)


def test_healthy_image_focus_is_not_degraded() -> None:
    _images, report = resolve_slide_images("finding_detail", {"screenshot_url": _GOOD_REF})
    assert (report.requested, report.failed) == (1, 0)


def test_a_healthy_scorecard_carries_no_marker() -> None:
    html, report = _html(
        "scorecard",
        {"title": "Q3", "metrics": [{"label": "MRR", "value": "9"}, {"label": "NPS"}]},
    )
    assert (report.offered, report.dropped) == (2, 0)
    assert _MARKER not in html


def test_a_text_only_slide_carries_no_marker() -> None:
    """Nothing offered is not the same as everything destroyed."""
    html, report = _html("title", {"title": "Just words"})
    assert report == SlideContentReport()
    assert _MARKER not in html


def test_over_limit_truncation_is_not_reported_as_degraded() -> None:
    """Showing six of eight metrics is the layout doing its job, not a defect."""
    metrics = [{"label": f"m{i}", "value": str(i)} for i in range(8)]
    html, report = _html("scorecard", {"title": "Q3", "metrics": metrics})
    assert report.dropped == 0
    assert _MARKER not in html


# ---------------------------------------------------------------------------
# Whole-deck: the manifest carries the count for the programmatic consumer
# ---------------------------------------------------------------------------


class _FakePage:
    async def set_content(self, _html: str, **_kw: Any) -> None:
        return None

    async def evaluate(self, _expr: str) -> None:
        return None

    async def screenshot(self, **_kw: Any) -> bytes:
        return b"\x89PNG\r\n\x1a\n"

    async def close(self) -> None:
        return None


class _FakeBrowser:
    async def new_page(self, **_kw: Any) -> _FakePage:
        return _FakePage()


class _FakePool:
    @asynccontextmanager
    async def lease(self):  # type: ignore[no-untyped-def]
        yield _FakeBrowser()


def _template() -> SimpleNamespace:
    return SimpleNamespace(
        template_id="t-1",
        name="scorecard",
        slot_definitions={},
        styling_rules={},
        default_transition={"type": "fade", "duration_seconds": 0.5},
        default_timing_hint_seconds=10.0,
        layout_type="scorecard",
        is_active=True,
    )


def _stub_storage() -> MagicMock:
    storage = MagicMock()
    storage.write_file = AsyncMock(return_value="p")
    storage.write_text = AsyncMock(return_value="m")
    storage.get_presigned_url = AsyncMock(return_value=None)
    return storage


async def _build(contents: list[dict[str, Any]]) -> dict[str, Any]:
    builder = DeckBuilder(Settings(), storage_service=_stub_storage())
    template_map = {"t-1": cast(Any, _template())}
    payload = {
        "title": "Deck",
        "slides": [
            {"template_id": "t-1", "content": c, "asset_urls": {}, "asset_placements": {}}
            for c in contents
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }
    with (
        patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as pdf,
        patch(
            "src.services.html_slide_renderer.get_browser_pool",
            new=AsyncMock(return_value=_FakePool()),
        ),
    ):
        pdf.return_value = b"%PDF-1.7 fake"
        return await builder.build_deck("job-c", payload, template_map, {"t-1": "v-1"})


@pytest.mark.asyncio
async def test_manifest_reports_the_dropped_entries() -> None:
    manifest = await _build(
        [
            {"title": "Good", "metrics": [{"label": "MRR", "value": "9"}]},
            {"title": "Destroyed", "metrics": ["a", "b"]},
        ]
    )
    assert manifest["content_entries_offered"] == 3
    assert manifest["content_entries_dropped"] == 2
    per_slide = [a["content_entries_dropped"] for a in manifest["assets"]["slides"]]
    assert per_slide == [0, 2]


@pytest.mark.asyncio
async def test_a_genuine_text_only_deck_is_not_degraded() -> None:
    """THE regression guard: a healthy text deck must stay 0/0 on every counter."""
    manifest = await _build([{"title": "Words only"}, {"title": "Still words"}])
    assert manifest["image_slots_requested"] == 0
    assert manifest["image_slots_failed"] == 0
    assert manifest["content_entries_offered"] == 0
    assert manifest["content_entries_dropped"] == 0


@pytest.mark.asyncio
async def test_a_fully_healthy_deck_is_unchanged() -> None:
    manifest = await _build(
        [
            {
                "title": "Q3",
                "metrics": [{"label": "MRR", "value": "9"}, {"label": "NPS", "value": "8"}],
            }
        ]
    )
    assert manifest["content_entries_offered"] == 2
    assert manifest["content_entries_dropped"] == 0


def test_manifest_response_exposes_the_content_counters() -> None:
    """Extra manifest keys are DROPPED by pydantic; a declared field is what exposes them."""
    from src.api.schemas import DeckManifestResponse

    resp = DeckManifestResponse(
        deck_id="d", title="t", content_entries_offered=5, content_entries_dropped=5
    )
    assert (resp.content_entries_offered, resp.content_entries_dropped) == (5, 5)
