"""An unbundled brand font must not degrade silently.

``_font_data_uri`` returns ``""`` for any family not in ``_FONT_FILES`` so an
unknown brand font can never break the render. That is correct, but it emitted
NO signal at all: the PNG falls back down the CSS stack while the PPTX path
(``BaseTemplateRenderer._get_font``) sets the requested family on every run, so
the two artifacts of the same deck disagree about the typeface and nothing says
so. ``BrandConfig.font_family`` defaults to ``"Arial"``, which is NOT bundled —
so this is the default path, not an exotic one.
"""

from __future__ import annotations

import logging

import pytest

from src.services.html_slide_renderer import _FONT_FILES, build_slide_html

_BRAND = {"primary_color": "#0B1F3A", "secondary_color": "#2BB6A3", "accent_color": "#F26B3A"}
_LOGGER = "src.services.html_slide_renderer"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_arial_is_not_bundled() -> None:
    """Guards the premise: the DEFAULT brand font is the unbundled case."""
    assert "arial" not in _FONT_FILES


def test_unbundled_display_font_warns_and_names_the_family(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        build_slide_html(
            layout_type="title",
            content={"title": "Hi"},
            brand={**_BRAND, "font_family": "Nonexistent Display Face"},
        )
    msgs = _warnings(caplog)
    assert any("Nonexistent Display Face" in m for m in msgs), msgs
    assert any("not bundled" in m for m in msgs), msgs
    # The message must state the disagreement, not just "font missing".
    assert any("disagree" in m and "pptx" in m.lower() for m in msgs), msgs


def test_unbundled_body_font_warns_separately(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        build_slide_html(
            layout_type="title",
            content={"title": "Hi"},
            brand={**_BRAND, "font_family": "Inter", "body_font_family": "Comic Sans MS"},
        )
    msgs = _warnings(caplog)
    assert any("Comic Sans MS" in m and "body" in m for m in msgs), msgs


def test_bundled_fonts_are_silent(caplog: pytest.LogCaptureFixture) -> None:
    """A fully bundled brand must emit nothing — a warning on the healthy path
    would be noise that trains an operator to ignore the signal."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        build_slide_html(
            layout_type="title",
            content={"title": "Hi"},
            brand={**_BRAND, "font_family": "Fraunces", "body_font_family": "IBM Plex Sans"},
        )
    assert _warnings(caplog) == []


def test_warning_does_not_change_the_rendered_html() -> None:
    """The signal is a log, not a counter and not a render change: an unbundled
    family still renders and still names itself in the CSS stack."""
    html = build_slide_html(
        layout_type="title",
        content={"title": "Hi"},
        brand={**_BRAND, "font_family": "Nonexistent Display Face"},
    )
    assert "font-family: 'Nonexistent Display Face'" not in html  # no @font-face
    assert '--font-display: "Nonexistent Display Face"' in html  # still in the stack
