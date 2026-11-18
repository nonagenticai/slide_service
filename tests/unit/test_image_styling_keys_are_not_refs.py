"""A LAYOUT HINT is not a lost image.

``image_fit``/``image_placement``/``image_style``/``image_scale`` are first-class
STYLING keys, resolved by ``src.services.image_layout``. Their values are
rendering semantics (``"contain"``, ``"full_bleed"``) — they point at no bytes.

They also contain the substring ``image``, and the unrenderable-slot counter
matched image slots by substring alone. So a ``stat``/``quote``/``timeline``
slide carrying ``image_fit`` and no actual image was counted
``requested=1, failed=1``; ``deck_builder._guard_image_slots`` then raised
``ValueError`` — AFTER the PPTX and every preview PNG had already been rendered
and written. Completed work, thrown away at the last gate, with
``failed_refs=("contain", ...)`` in the message.

The fix classifies at the source of truth: ``image_layout`` is where the hint
keys are CONSUMED, so it is the module that publishes them
(:data:`IMAGE_LAYOUT_HINT_KEYS`, derived from the very tuples its resolvers
iterate). Value SHAPE is the second gate, so an unregistered key still cannot be
mistaken for a lost image — it lands in a third, explicitly counted state.
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
from src.services.html_slide_renderer import SlideImageReport, resolve_slide_images
from src.services.image_layout import (
    IMAGE_FIT_HINT_KEYS,
    IMAGE_LAYOUT_HINT_KEYS,
    IMAGE_PLACEMENT_HINT_KEYS,
    ImageSlotKind,
    classify_image_slot,
    resolve_image_fit,
    resolve_image_placement,
)

_BAD_REF = "http://[::1"  # urlparse rejects it => load_image_bytes returns None
_GOOD_REF = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "value"),
    [
        ("image_fit", "contain"),
        ("image_placement", "full_bleed"),
        ("image_style", "framed"),
        ("image_scale", "cover"),
    ],
)
def test_a_styling_key_is_a_layout_hint_not_a_reference(slot: str, value: str) -> None:
    assert classify_image_slot(slot, value) is ImageSlotKind.LAYOUT_HINT


@pytest.mark.parametrize(
    "value",
    ["https://example.test/a.png", "data:image/png;base64,AAAA", "asset://hero", "decks/x/a.jpg"],
)
def test_a_reference_shaped_value_is_a_reference(value: str) -> None:
    assert classify_image_slot("image_url", value) is ImageSlotKind.REFERENCE


@pytest.mark.parametrize(
    "value",
    [
        "decks/job 1/a.png",  # a path with a space is STILL a path
        "https://minio.test/bucket/my deck/slide 1.png?X-Amz-Signature=ab cd",
        "hero image.png",  # no separator, but an image extension
    ],
)
def test_whitespace_alone_does_not_disqualify_a_reference(value: str) -> None:
    """Object keys and filenames legitimately contain spaces.

    Rejecting on whitespace BEFORE testing the positive signals demoted a real,
    unloadable reference to UNCLASSIFIED, where it escaped the all-failed veto
    and the deck shipped missing its imagery — the same fail-open shape this fix
    removes.
    """
    assert classify_image_slot("image_url", value) is ImageSlotKind.REFERENCE


def test_a_whitespace_only_value_is_not_a_slot() -> None:
    """No signal at all: not a reference, not a hint, not something to count."""
    assert classify_image_slot("image_url", "   ") is ImageSlotKind.NOT_A_SLOT
    _images, report = resolve_slide_images("stat", {"value": "1", "image_url": "  \t "})
    assert report == SlideImageReport()


def test_a_spaced_path_that_cannot_load_still_fails_the_slot() -> None:
    """CONTROL for the whitespace hole, at the counter.

    ``finding_detail`` maps to the ``image_focus`` family; ``stat`` does not. ``stat`` draws no image at all,
    so ``load_image_bytes`` is never called on it and the slot can only ever be
    UNDRAWN — this control used to assert ``failed=1`` on a path that never
    attempted a load, so it proved nothing about loading. ``image_focus`` draws
    ``image_url``, so the ref is genuinely fetched and genuinely lost.
    """
    _images, report = resolve_slide_images(
        "finding_detail", {"title": "t", "image_url": "decks/j 1/a.png"}
    )
    assert (report.requested, report.failed, report.unclassified) == (1, 1, 0)
    assert report.undrawn == 0, "the family DOES draw this slot; it was fetched and lost"


def test_a_spaced_path_on_a_family_that_draws_nothing_is_undrawn_not_failed() -> None:
    """The same ref on ``stat``: still a reference, but nothing fetched it.

    Reporting this as a failed LOAD asserts a fact never established — the path
    may resolve perfectly well; no renderer ever asked.
    """
    _images, report = resolve_slide_images("stat", {"value": "1", "image_url": "decks/j 1/a.png"})
    assert (report.requested, report.failed, report.unclassified) == (0, 0, 0)
    assert (report.undrawn, report.undrawn_slots) == (1, ("image_url",))


def test_a_bare_unresolvable_asset_id_is_unclassified() -> None:
    """PINNED, deliberate: `main` counted this failed; it is now UNCLASSIFIED.

    A bare word under an image-ish key is shape-identical to a styling value, so
    the only rule that would call it a lost image is the substring guess this fix
    removes — it would fire on the next unregistered styling key.

    This path is reachable through the ORDINARY contract, not only by bypassing
    it: `_resolve_asset_reference` falls back to the raw string on a catalog
    miss, and the `asset_urls` loop writes that into `content[slot]`. So
    `asset_urls: {"image_url": "hero-1"}` with no matching `image_assets` entry
    arrives here as a bare id. `main` VETOED that deck; this branch warns
    (twice) and ships it, with the count on the manifest. Deliberate narrowing,
    not an oversight.
    """
    _images, report = resolve_slide_images("stat", {"value": "1", "image_url": "hero-1"})
    assert (report.requested, report.failed) == (0, 0)
    assert report.unclassified == 1
    # The documented spelling of the same intent IS a reference and still fails —
    # asserted on ``finding_detail`` (the ``image_focus`` family), which draws
    # ``image_url`` and so actually attempts the load. On ``stat`` nothing
    # fetches it, so the only honest answer there is UNDRAWN, not "failed".
    _images, resolved = resolve_slide_images(
        "finding_detail", {"title": "t", "image_url": "asset://hero-1"}
    )
    assert (resolved.requested, resolved.failed) == (1, 1)


def test_an_unregistered_image_key_with_no_reference_shape_is_unclassified() -> None:
    """Fail-open and fail-closed are BOTH wrong here, so the gap gets its own state."""
    kind = classify_image_slot("image_treatment", "duotone")
    assert kind is ImageSlotKind.UNCLASSIFIED
    assert kind is not ImageSlotKind.REFERENCE  # not a satisfied/lost image
    assert kind is not ImageSlotKind.NOT_A_SLOT  # not silently dropped either


def test_a_plain_content_key_is_not_a_slot() -> None:
    assert classify_image_slot("title", "Quarterly review") is ImageSlotKind.NOT_A_SLOT


@pytest.mark.parametrize("key", sorted(IMAGE_LAYOUT_HINT_KEYS))
def test_every_published_hint_key_is_excluded_from_reference_counting(key: str) -> None:
    """The published set and the counter cannot drift: one derives from the other."""
    assert classify_image_slot(key, "contain") is ImageSlotKind.LAYOUT_HINT


@pytest.mark.parametrize("key", IMAGE_PLACEMENT_HINT_KEYS)
def test_every_placement_key_the_resolver_reads_is_published(key: str) -> None:
    """A key is a hint BECAUSE image_layout consumes it — prove both halves."""
    assert resolve_image_placement({key: "full_bleed"}, None) == "full_bleed"
    assert key in IMAGE_LAYOUT_HINT_KEYS


@pytest.mark.parametrize("key", IMAGE_FIT_HINT_KEYS)
def test_every_fit_key_the_resolver_reads_is_published(key: str) -> None:
    assert resolve_image_fit({key: "contain"}, None) == "contain"
    assert key in IMAGE_LAYOUT_HINT_KEYS


# ---------------------------------------------------------------------------
# Slot report
# ---------------------------------------------------------------------------


def test_styling_keys_on_a_non_image_family_request_nothing() -> None:
    """THE BUG: a stat slide with layout hints and no image asked for no image."""
    _images, report = resolve_slide_images(
        "stat",
        {
            "value": "42",
            "label": "users",
            "image_fit": "contain",
            "image_placement": "full_bleed",
        },
    )
    assert report == SlideImageReport()


@pytest.mark.parametrize(
    ("family", "content"),
    [
        (
            "comparison",
            {"before_image_url": _GOOD_REF, "after_image_url": _GOOD_REF, "image_fit": "contain"},
        ),
        ("finding_detail", {"screenshot_url": _GOOD_REF, "image_placement": "full_bleed"}),
    ],
)
def test_a_styling_hint_beside_a_drawn_image_is_not_a_lost_slot(
    family: str, content: dict[str, str]
) -> None:
    """The families that DO draw images now reach the sweep too (main #122).

    Before #122 the `comparison` and `image_focus` branches returned early, so
    the unrenderable-slot sweep never ran on them and a styling hint there was
    harmless by accident. #122 routes every family through one tail, which makes
    the hint reachable on exactly the slides most likely to carry one — a deck
    whose images all LOADED would have been failed by its own layout hint.
    """
    _images, report = resolve_slide_images(family, content)
    assert report.failed == 0
    assert report.unclassified == 0
    assert report.requested == sum(1 for k in content if k.endswith("_url"))


def test_a_real_missing_image_on_a_family_that_draws_it_still_fails() -> None:
    """CONTROL: the guard is narrowed, not disabled.

    Was written against ``quote``, which draws no image — so it asserted a
    failed LOAD on a path with no load in it. ``finding_detail`` (the
    ``image_focus`` family) draws the slot, so ``_BAD_REF`` is really fetched
    and really returns no bytes.
    """
    _images, report = resolve_slide_images("finding_detail", {"title": "hi", "image_url": _BAD_REF})
    assert (report.requested, report.failed) == (1, 1)
    assert report.undrawn == 0


def test_an_unclassified_slot_is_counted_but_is_neither_requested_nor_failed() -> None:
    _images, report = resolve_slide_images("stat", {"value": "1", "image_treatment": "duotone"})
    assert (report.requested, report.failed) == (0, 0)
    assert (report.unclassified, report.unclassified_slots) == (1, ("image_treatment",))


def test_reports_merge_the_unclassified_counters() -> None:
    merged = SlideImageReport(unclassified=1, unclassified_slots=("a",)).merge(
        SlideImageReport(unclassified=2, unclassified_slots=("b", "c"))
    )
    assert (merged.unclassified, merged.unclassified_slots) == (3, ("a", "b", "c"))


# ---------------------------------------------------------------------------
# Whole-deck: the work must survive the last gate
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


def _template(layout: str) -> SimpleNamespace:
    return SimpleNamespace(
        template_id="t-1",
        name=layout,
        slot_definitions={},
        styling_rules={},
        default_transition={"type": "fade", "duration_seconds": 0.5},
        default_timing_hint_seconds=10.0,
        layout_type=layout,
        is_active=True,
    )


def _stub_storage() -> MagicMock:
    storage = MagicMock()
    storage.write_file = AsyncMock(return_value="p")
    storage.write_text = AsyncMock(return_value="m")
    storage.get_presigned_url = AsyncMock(return_value=None)
    return storage


async def _build(layout: str, contents: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "title": "Styling deck",
        "slides": [
            {
                "template_id": "t-1",
                "content": content,
                "asset_urls": {},
                "asset_placements": {},
            }
            for content in contents
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }
    builder = DeckBuilder(Settings(), storage_service=_stub_storage())
    template_map = {"t-1": cast(Any, _template(layout))}
    with (
        patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as pdf,
        patch(
            "src.services.html_slide_renderer.get_browser_pool",
            new=AsyncMock(return_value=_FakePool()),
        ),
    ):
        pdf.return_value = b"%PDF-1.7 fake"
        return await builder.build_deck("job-style", payload, template_map, {"t-1": "v-1"})


@pytest.mark.asyncio
async def test_a_deck_of_styling_hints_and_no_images_builds() -> None:
    """The regression: this raised ValueError after every artifact was written."""
    manifest = await _build(
        "stat",
        [
            {"title": "A", "value": "42", "image_fit": "contain"},
            {"title": "B", "value": "7", "image_placement": "full_bleed", "image_style": "framed"},
        ],
    )
    assert manifest["image_slots_requested"] == 0
    assert manifest["image_slots_failed"] == 0
    assert manifest["image_slots_unclassified"] == 0
    # ... and the hint must not have been published as an external image asset.
    assert "contain" not in [a["image_url"] for a in manifest["assets"].get("images", [])]


@pytest.mark.asyncio
async def test_a_deck_whose_only_real_image_is_missing_still_fails() -> None:
    """CONTROL: a genuine ref on a family that DRAWS it still hard-fails the deck.

    ``finding_detail`` (image_focus family), not ``stat``: on ``stat`` nothing
    fetches the ref, so the veto would fire on a load that never happened.
    """
    with pytest.raises(ValueError, match="every requested image failed to load"):
        await _build("finding_detail", [{"title": "A", "image_url": _BAD_REF}])


@pytest.mark.asyncio
async def test_a_mixed_slide_counts_the_ref_and_ignores_the_hint() -> None:
    """A hint sitting NEXT TO a live ref must not inflate the counters."""
    manifest = await _build(
        "finding_detail",
        [
            {"title": "A", "screenshot_url": _GOOD_REF, "image_fit": "contain"},
            {"title": "B", "image_placement": "full_bleed"},
        ],
    )
    assert manifest["image_slots_requested"] == 1
    assert manifest["image_slots_failed"] == 0
    per_slide = [a["image_slots_requested"] for a in manifest["assets"]["slides"]]
    assert per_slide == [1, 0]


@pytest.mark.asyncio
async def test_a_deck_whose_only_image_is_a_spaced_path_still_fails() -> None:
    """CONTROL for the whitespace hole, end to end: the veto must still fire.

    On ``finding_detail`` (image_focus family), which draws the slot — so the
    veto fires on a real, attempted, failed load, not a slot nobody rendered.
    """
    with pytest.raises(ValueError, match="every requested image failed to load"):
        await _build("finding_detail", [{"title": "A", "image_url": "decks/job 1/a.png"}])


@pytest.mark.asyncio
async def test_a_deck_whose_only_image_slot_is_undrawn_is_not_vetoed() -> None:
    """The same ref on a family that draws nothing must NOT fail the deck.

    Nothing was fetched, so "every requested image failed to load" would be a
    false statement. The deck ships and the manifest carries the real reason.
    """
    manifest = await _build(
        "stat", [{"title": "A", "value": "42", "image_url": "decks/job 1/a.png"}]
    )
    assert manifest["image_slots_failed"] == 0
    assert manifest["image_slots_requested"] == 0
    assert manifest["image_slots_undrawn"] == 1


@pytest.mark.asyncio
async def test_an_unclassified_slot_ships_the_deck_but_is_reported() -> None:
    manifest = await _build("stat", [{"title": "A", "value": "42", "image_treatment": "duotone"}])
    assert manifest["image_slots_unclassified"] == 1
    assert manifest["image_slots_failed"] == 0
