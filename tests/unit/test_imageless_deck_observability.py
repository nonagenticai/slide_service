"""An imageless deck must be distinguishable from an intended text-only deck.

``load_image_bytes`` degrades every failure (expired/403 presigned MinIO URL,
SSRF rejection, timeout) to ``None``. ``_image_slot`` then sets
``has_image=False`` and the ``image_focus`` template drops the ``<img>`` and
reflows to text. Nothing counted the failed slots, so a deck whose EVERY image
failed returned 200, reached ``completed``, and looked deliberate.

Presigned URLs expire and every prod ``cut_plan`` run is more than a day old, so
the expired branch is the COMMON case — a silent all-images-failed deck is the
expected outcome, not an exotic one.
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

# A ref urlparse itself rejects ("Invalid IPv6 URL") => load_image_bytes returns
# None with no network access at all. Stands in for the expired-presigned branch.
_BAD_REF = "http://[::1"
_GOOD_REF = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


# ---------------------------------------------------------------------------
# Resolver level: the report exists and counts
# ---------------------------------------------------------------------------


def test_resolve_slide_images_counts_a_failed_slot() -> None:
    """A supplied-but-unloadable ref is REQUESTED and FAILED, not simply absent."""
    images, report = resolve_slide_images("finding_detail", {"screenshot_url": _BAD_REF})

    assert images["image"] is not None
    assert images["image"]["has_image"] is False
    assert report.requested == 1
    assert report.failed == 1
    assert report.failed_refs == (_BAD_REF,)


def test_resolve_slide_images_no_ref_is_not_a_failure() -> None:
    """A deliberate text-only slide requests nothing, so nothing can fail."""
    _images, report = resolve_slide_images("finding_detail", {"title": "No image here"})
    assert report == SlideImageReport()


def test_resolve_slide_images_success_is_not_a_failure() -> None:
    """A ref that loads counts as requested but not failed."""
    _images, report = resolve_slide_images("finding_detail", {"screenshot_url": _GOOD_REF})
    assert (report.requested, report.failed) == (1, 0)


def test_resolve_slide_images_counts_both_comparison_slots() -> None:
    """The comparison family has two slots; a partial failure is 1 of 2."""
    _images, report = resolve_slide_images(
        "comparison", {"before_image_url": _GOOD_REF, "after_image_url": _BAD_REF}
    )
    assert (report.requested, report.failed) == (2, 1)


# ---------------------------------------------------------------------------
# Production call site: deck_builder must READ the report
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
        name="finding_detail",
        slot_definitions={},
        styling_rules={},
        default_transition={"type": "fade", "duration_seconds": 0.5},
        default_timing_hint_seconds=10.0,
        layout_type="finding_detail",
        is_active=True,
    )


def _payload(refs: list[str]) -> dict[str, Any]:
    return {
        "title": "Screenshot deck",
        "slides": [
            {
                "template_id": "t-1",
                "content": {"title": f"Shot {i}", "screenshot_url": ref},
                "asset_urls": {},
                "asset_placements": {},
            }
            for i, ref in enumerate(refs)
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }


def _stub_storage() -> MagicMock:
    storage = MagicMock()
    storage.write_file = AsyncMock(return_value="p")
    storage.write_text = AsyncMock(return_value="m")
    storage.get_presigned_url = AsyncMock(return_value=None)
    return storage


async def _build(refs: list[str]) -> dict[str, Any]:
    builder = DeckBuilder(Settings(), storage_service=_stub_storage())
    template_map = {"t-1": cast(Any, _template())}
    with (
        patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as pdf,
        patch(
            "src.services.html_slide_renderer.get_browser_pool",
            new=AsyncMock(return_value=_FakePool()),
        ),
    ):
        pdf.return_value = b"%PDF-1.7 fake"
        return await builder.build_deck("job-img", _payload(refs), template_map, {"t-1": "v-1"})


@pytest.mark.asyncio
async def test_deck_with_every_image_failed_is_not_reported_complete() -> None:
    """The whole-deck case: every screenshot expired => the job must FAIL loudly.

    Before the fix this returned a manifest, the job row went to ``completed``,
    and the caller received a text-only deck indistinguishable from one it asked
    for.
    """
    with pytest.raises(ValueError, match="image"):
        await _build([_BAD_REF, _BAD_REF, _BAD_REF])


@pytest.mark.asyncio
async def test_partial_image_failure_ships_but_is_counted() -> None:
    """A deck that mostly worked still ships — with the failure count in the manifest."""
    manifest = await _build([_GOOD_REF, _BAD_REF, _GOOD_REF])

    assert manifest["image_slots_requested"] == 3
    assert manifest["image_slots_failed"] == 1
    failed_per_slide = [a["image_slots_failed"] for a in manifest["assets"]["slides"]]
    assert failed_per_slide == [0, 1, 0]


@pytest.mark.asyncio
async def test_text_only_deck_reports_zero_requested() -> None:
    """A deliberate text deck requests zero image slots and must NOT hard-fail."""
    manifest = await _build(["", ""])
    assert manifest["image_slots_requested"] == 0
    assert manifest["image_slots_failed"] == 0


# ---------------------------------------------------------------------------
# The manifest API response must carry the counters (extra keys are DROPPED by
# pydantic, so a field on DeckManifestResponse is what makes them reachable).
# ---------------------------------------------------------------------------


def test_manifest_response_exposes_image_counters() -> None:
    from src.api.schemas import DeckManifestResponse

    resp = DeckManifestResponse(
        deck_id="d", title="t", image_slots_requested=4, image_slots_failed=4
    )
    assert (resp.image_slots_requested, resp.image_slots_failed) == (4, 4)


# ---------------------------------------------------------------------------
# A requested image on a family with nowhere to draw it must still COUNT
# ---------------------------------------------------------------------------
#
# Every test above (and every image assertion in test_deck_builder.py) drives
# `finding_detail` / `comparison` — i.e. the ONLY two families whose branch in
# `resolve_slide_images` builds a slot report at all. Nothing exercised the
# terminal branch, so `requested == 0` on a stat/bullets/title slide carrying an
# image ref went unnoticed. `deck_builder._apply_slide_asset_contract` accepts an
# image slot on ANY layout (it matches purely on the slot NAME), so this is a
# reachable caller shape, not a hypothetical one.


def test_image_ref_on_a_non_image_family_is_undrawn_not_failed() -> None:
    """A `stat` slide carrying `image_url` has an UNDRAWN slot, not a lost image.

    Inverted deliberately. This used to assert ``failed=1`` with
    ``failed_refs=(_GOOD_REF,)`` — and ``_GOOD_REF`` is a data URI that loads
    perfectly well. Nothing ever fetched it: ``stat`` has no placeholder, so
    ``load_image_bytes`` is not called on this path at all. Calling it a failed
    load was a claim about the image made with zero evidence about the image,
    and it fed a hard-fail gate.

    The loss stays VISIBLE — that requirement has not changed — it is just
    recorded under the cause that is actually true.
    """
    images, report = resolve_slide_images("stat", {"value": "42", "image_url": _GOOD_REF})

    # The family genuinely has no placeholder — that is not what is under test.
    assert images == {}
    assert (report.requested, report.failed, report.failed_refs) == (0, 0, ())
    assert (report.undrawn, report.undrawn_slots) == (1, ("image_url",))
    # Still not silently identical to a deliberately text-only slide.
    assert report != SlideImageReport()


def test_text_only_non_image_family_still_requests_nothing() -> None:
    """The counter must not misfire on a genuinely text-only slide."""
    _images, report = resolve_slide_images("stat", {"value": "42", "label": "users"})
    assert report == SlideImageReport()


def test_non_image_family_ignores_blank_and_non_string_slots() -> None:
    """An empty ref or a non-string value is not a request."""
    _images, report = resolve_slide_images(
        "stat",
        {"image_url": "   ", "asset_placements": {"image_url": "left"}, "screenshot_url": None},
    )
    assert report == SlideImageReport()


def test_an_undrawn_slot_does_not_make_the_all_failed_guard_fire() -> None:
    """End of the chain, INVERTED: an undrawn slot must not veto the deck.

    This is the reported production shape — a title slide carrying a brand
    logo. The old assertion here was ``pytest.raises(ValueError, match="every
    requested image failed to load")``, so a title slide with a live logo URL
    failed its entire job. That message was false on both counts: no image was
    requested of the loader, and none failed to load.

    The all-failed veto is reserved for images that were fetched and lost; see
    ``test_all_failed_guard_still_fires_on_a_genuinely_dead_image`` for the
    control proving it still fires.
    """
    _images, report = resolve_slide_images("title", {"title": "Hi", "logo_url": _GOOD_REF})
    builder = DeckBuilder(Settings())

    builder._guard_image_slots("job-1", report)  # must not raise

    assert (report.undrawn, report.undrawn_slots) == (1, ("logo_url",))
    assert (report.requested, report.failed) == (0, 0)


def test_all_failed_guard_still_fires_on_a_genuinely_dead_image() -> None:
    """CONTROL: the veto is narrowed, not disabled.

    ``finding_detail`` (the ``image_focus`` family) draws ``image_url``, so
    ``_BAD_REF`` is really fetched and really returns no bytes — the one
    situation the guard exists for.
    """
    _images, report = resolve_slide_images("finding_detail", {"title": "Hi", "image_url": _BAD_REF})
    builder = DeckBuilder(Settings())

    assert (report.requested, report.failed) == (1, 1)
    with pytest.raises(ValueError, match="every requested image failed to load"):
        builder._guard_image_slots("job-1", report)
