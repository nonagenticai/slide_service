"""One malformed image ref must degrade to a placeholder, not abort the deck.

``deck_builder`` renders every slide in a bare loop with no per-slide
try/except, so anything escaping ``load_image_bytes`` aborts the WHOLE deck.
Two escape routes existed:

1. ``urlparse`` itself raises ``ValueError`` on some malformed refs (e.g. the
   unterminated IPv6 literal ``http://[::1`` -> "Invalid IPv6 URL"), and that
   call sat OUTSIDE the fetch try-block entirely.
2. ``httpx.InvalidURL`` is NOT a subclass of ``httpx.HTTPError`` (it derives
   straight from ``Exception``), so it slipped past the fetch except-tuple.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pptx import Presentation

from src.services.image_resolver import load_image_bytes
from src.services.template_renderer import render_slide

_BRAND = {"primary_color": "#123456", "font_family": "Inter"}


# ---------------------------------------------------------------------------
# Resolver level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_ref",
    [
        "http://[::1",  # unterminated IPv6 literal -> urlparse ValueError
        "https://[fe80::1",
    ],
)
def test_unparseable_url_returns_none(bad_ref: str) -> None:
    """A ref that ``urlparse`` itself rejects degrades to the placeholder path."""
    assert load_image_bytes(bad_ref) is None


def test_httpx_invalid_url_returns_none() -> None:
    """``httpx.InvalidURL`` is not an ``httpx.HTTPError`` — it must still be caught."""
    assert not issubclass(httpx.InvalidURL, httpx.HTTPError)  # guards the premise
    with patch(
        "src.services.image_resolver.safe_get_sync",
        MagicMock(side_effect=httpx.InvalidURL("no host in URL")),
    ):
        assert load_image_bytes("https://example.com/image.png") is None


def test_type_error_from_fetch_returns_none() -> None:
    """A ``TypeError`` raised while building the request also degrades."""
    with patch(
        "src.services.image_resolver.safe_get_sync",
        MagicMock(side_effect=TypeError("not a valid url type")),
    ):
        assert load_image_bytes("https://example.com/image.png") is None


# ---------------------------------------------------------------------------
# Deck level — the behaviour that actually matters
# ---------------------------------------------------------------------------


def test_one_bad_image_ref_does_not_abort_the_deck() -> None:
    """Slide 2 carries a malformed image ref; slides 1 and 3 must still render.

    This mirrors ``deck_builder``'s render loop, which has no try/except: before
    the fix the malformed ref raised out of ``render_slide`` and slide 3 was
    never reached, i.e. ONE bad ref destroyed the whole deck.
    """
    slides = [
        {"title": "Good one", "body": "fine", "image_url": ""},
        {"title": "Bad ref", "body": "malformed", "image_url": "http://[::1"},
        {"title": "Good two", "body": "fine", "image_url": ""},
    ]

    prs = Presentation()
    for slide_data in slides:
        render_slide(
            prs,
            layout_type="feature_spotlight",
            content=slide_data,
            brand=_BRAND,
            styling_rules={},
        )

    assert len(prs.slides) == 3
