"""Tests for the HTML/CSS + Chromium slide renderer.

Two tiers:

* **Pure HTML** (no browser) — run everywhere incl. browserless CI. They guard
  the actual defects the PIL renderer had: title truncation ("…") and the
  mid-word URL break, plus that every layout family renders.
* **Real Chromium** — guarded by ``skipif(not _chromium_available())`` so they
  skip on a CI runner that hasn't run ``playwright install chromium`` and only
  run locally / inside the built image (same pattern as html_render_service).
"""

from __future__ import annotations

import contextlib
import struct
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.services.html_slide_renderer import build_slide_html, render_slide_png

_BRAND = {"primary_color": "#0B1F3A", "secondary_color": "#2BB6A3", "accent_color": "#F26B3A"}
_LONG_URL = "commonpractice.nonagentic.ai/get-started/with-a-really-long-path-segment"
_LONG_TITLE = (
    "The people closest to the work decide what is actually worth improving every single day"
)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        return bool(path) and Path(path).exists()
    except Exception:  # noqa: BLE001 -- availability probe: any failure => unavailable
        return False


def _png_size(data: bytes) -> tuple[int, int]:
    """Return (width, height) from a PNG's IHDR chunk — no Pillow needed."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# --------------------------------------------------------------------------
# Pure HTML — run in CI (no browser)
# --------------------------------------------------------------------------


def test_cta_long_url_wraps_and_is_not_truncated() -> None:
    html = build_slide_html(
        layout_type="cta",
        content={"eyebrow": "Common Practice", "title": "Start today", "cta_url": _LONG_URL},
        brand=_BRAND,
    )
    # The mid-word-break fix: the URL element wraps at the box edge by construction.
    assert "overflow-wrap: anywhere" in html
    # The full URL survives — never truncated, never elided.
    assert _LONG_URL in html
    assert "…" not in html


def test_title_long_line_is_not_truncated() -> None:
    html = build_slide_html(
        layout_type="title",
        content={"eyebrow": "Common Practice", "title": _LONG_TITLE},
        brand=_BRAND,
    )
    assert _LONG_TITLE in html
    assert "…" not in html


def test_font_is_embedded() -> None:
    html = build_slide_html(layout_type="title", content={"title": "Hi"}, brand=_BRAND)
    assert "@font-face" in html
    assert "data:font/woff2;base64," in html


def test_brand_display_and_body_fonts_both_embed() -> None:
    """A brand asking for Fraunces (display) + IBM Plex Sans (body) embeds BOTH
    faces and wires the title to the display font — the SpecraLabs slide-font fix.
    """
    brand = {**_BRAND, "font_family": "Fraunces", "body_font_family": "IBM Plex Sans"}
    html = build_slide_html(layout_type="title", content={"title": "Hi"}, brand=brand)
    # Two distinct @font-face blocks, one per family, each with its own data URI.
    assert html.count("@font-face") == 2
    assert "font-family: 'Fraunces'" in html
    assert "font-family: 'IBM Plex Sans'" in html
    assert html.count("data:font/woff2;base64,") == 2
    # Titles bind to the display face; body to the body face.
    assert '--font-display: "Fraunces"' in html
    assert '--font-body: "IBM Plex Sans"' in html
    assert ".title { font-family: var(--font-display); }" in html


def test_unbundled_brand_font_falls_back_without_breaking() -> None:
    """An unbundled display font emits no @font-face for it but still renders
    (CSS stack falls back to Inter/system) — an unknown brand font can't 500."""
    brand = {**_BRAND, "font_family": "Nonexistent Display Face"}
    html = build_slide_html(layout_type="title", content={"title": "Hi"}, brand=brand)
    assert "font-family: 'Nonexistent Display Face'" not in html  # no @font-face emitted
    assert '--font-display: "Nonexistent Display Face"' in html  # still in the CSS stack


def test_brand_colors_flow_into_css() -> None:
    html = build_slide_html(layout_type="title", content={"title": "Hi"}, brand=_BRAND)
    assert "#0B1F3A" in html  # primary
    assert "#2BB6A3" in html  # secondary


@pytest.mark.parametrize(
    "layout_type",
    [
        "title",
        "agenda",
        "process_flow",
        "roadmap",
        "scorecard",
        "metric_summary",
        "comparison",
        "finding_detail",
        "feature_spotlight",
        "tutorial_step",
        "cta",
        # SLIDE-2 families + their aliases
        "quote",
        "testimonial",
        "stat",
        "metric_hero",
        "section_divider",
        "timeline",
        "two_up",
        "split",
        "unknown_falls_back_to_title",
    ],
)
def test_every_layout_family_renders(layout_type: str) -> None:
    html = build_slide_html(
        layout_type=layout_type,
        content={
            "title": "Sample",
            "subtitle": "Body",
            "metrics": [{"label": "Score", "value": "9.1"}],
            "caption": "cap",
        },
        brand=_BRAND,
    )
    assert html.lstrip().startswith("<!doctype html>")
    assert '<div class="slide">' in html


def test_image_focus_renders_bullets() -> None:
    """image_focus (feature_spotlight/tutorial_step) renders a text bullet list.

    Regression: the PNG path previously dropped ``bullets`` entirely (only the
    PPTX renderer drew them), so lesson slides showed an empty image placeholder
    and no body. Bullets accept plain strings or ``{"text": ...}`` dicts.
    """
    html = build_slide_html(
        layout_type="feature_spotlight",
        content={
            "title": "What is non-agentic AI",
            "bullets": [
                "Operates request-response, no autonomy",
                {"text": "Predictable and easy to reason about"},
                "",  # dropped
            ],
        },
        brand=_BRAND,
        images={"image": None},
    )
    assert '<ul class="bullets">' in html
    assert "Operates request-response, no autonomy" in html
    assert "Predictable and easy to reason about" in html
    # empty bullet dropped → exactly two <li>
    assert html.count("<li>") == 2


def test_image_focus_no_bullets_block_when_absent() -> None:
    html = build_slide_html(
        layout_type="feature_spotlight",
        content={"title": "No bullets here"},
        brand=_BRAND,
        images={"image": None},
    )
    assert '<ul class="bullets">' not in html


def test_no_image_collapses_to_full_width_text() -> None:
    """image_focus with no resolved image drops the right panel entirely and
    widens the text to a deliberate full-width slide — no gray placeholder box.
    (Regression: the recap 'bullets' card used to render 'Screenshot preview
    unavailable' because the right half rendered unconditionally.)"""
    html = build_slide_html(
        layout_type="finding_detail",
        content={"title": "A finding"},
        brand=_BRAND,
        images={"image": None},
    )
    assert "Screenshot preview unavailable" not in html
    assert 'class="left full"' in html  # text panel widened
    assert 'class="right' not in html  # right panel omitted


def test_feature_spotlight_bullets_full_width_when_no_image() -> None:
    """The recap card path (bullets -> feature_spotlight) with no image renders
    the bullets full-width with no placeholder panel."""
    html = build_slide_html(
        layout_type="feature_spotlight",
        content={"title": "Recap", "bullets": ["One", "Two", "Three"]},
        brand=_BRAND,
        images={"image": None},
    )
    assert "Screenshot preview unavailable" not in html
    assert 'class="left full"' in html
    assert 'class="right' not in html
    assert '<ul class="bullets">' in html
    assert html.count("<li>") == 3


def test_right_panel_present_when_image_resolved() -> None:
    """With a resolved image the two-panel layout is intact (no regression to the
    normal image_focus rendering)."""
    html = build_slide_html(
        layout_type="feature_spotlight",
        content={"title": "Feature"},
        brand=_BRAND,
        images={
            "image": {
                "has_image": True,
                "data_uri": "data:image/png;base64,AAAA",
                "object_fit": "cover",
                "full_bleed": False,
            }
        },
    )
    assert 'class="right framed"' in html
    assert 'class="left full"' not in html
    assert "data:image/png;base64,AAAA" in html


# --------------------------------------------------------------------------
# Design tokens (SLIDE-1) — the receiving surface for the orchestrator's
# design system. Presence is opt-in; absence must be byte-identical to before.
# --------------------------------------------------------------------------

# The ``:root`` block exactly as it renders today for ``_BRAND`` with no tokens.
# If a token leaks into a token-less render, this fails.
_ROOT_BLOCK_NO_TOKENS = (
    ":root {\n"
    "  --primary: #0B1F3A;\n"
    "  --secondary: #2BB6A3;\n"
    "  --accent: #F26B3A;\n"
    '  --font-display: "Inter", "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;\n'
    '  --font-body: "Inter", "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;\n'
    "  /* Back-compat alias: templates predating the display/body split use var(--font). */\n"
    "  --font: var(--font-body);\n"
    "}"
)

_TOKENS = {
    "surface": "#FFFFFF",
    "surface_2": "#F4F6F8",
    "on_surface": "#101418",
    "on_surface_muted": "#5A6472",
    # The real design_system.as_params() emits an `accent` key, which kebab-cases
    # onto the SAME `--accent` the brand channel declares. Keep it here so the
    # collision is actually exercised — see the cascade-order test below.
    "accent": "#00FF00",
    "accent_2": "#7C5CFF",
    "accent_soft": "rgba(124, 92, 255, 0.12)",
    "type_h1": "clamp(2.2rem, 3.8vw, 3.6rem)",
    "type_body": "1.8rem",
    "grid_gutter": "32px",
    "grid_margin": "96px",
    "type_scale_ratio": "1.25",
}


@pytest.mark.parametrize(
    "layout_type", ["title", "cta", "comparison", "scorecard", "feature_spotlight"]
)
def test_no_tokens_emits_no_design_token_vars(layout_type: str) -> None:
    """``tokens=None`` => the :root block is exactly today's, so every template's
    ``var(--x, <literal>)`` falls back and the render is pixel-identical."""
    html = build_slide_html(
        layout_type=layout_type,
        content={"title": "Hi", "metrics": [{"label": "Score", "value": "9.1"}]},
        brand=_BRAND,
        tokens=None,
    )
    assert _ROOT_BLOCK_NO_TOKENS in html
    for declared in ("--surface:", "--on-surface:", "--type-h1:", "--grid-gutter:", "--accent-2:"):
        assert declared not in html


def test_var_fallbacks_carry_todays_literal_values() -> None:
    """Each converted declaration keeps its pre-token literal as the CSS fallback."""
    scorecard = build_slide_html(
        layout_type="scorecard",
        content={"title": "S", "metrics": [{"label": "L", "value": "1"}], "footer": "f"},
        brand=_BRAND,
    )
    assert "background: var(--surface, #f8fafc)" in scorecard
    assert "background: var(--surface-2, #fff)" in scorecard
    assert "padding: 7vh var(--grid-margin, 6vw)" in scorecard
    assert "gap: var(--grid-gutter, 2vw)" in scorecard
    assert "font-size: var(--type-h1, clamp(2rem, 3.6vw, 3.4rem))" in scorecard
    assert "color: var(--on-surface-muted, #57647a)" in scorecard

    title = build_slide_html(layout_type="title", content={"title": "T"}, brand=_BRAND)
    assert "font-size: var(--type-display, clamp(2.5rem, 6.2vw, 6rem))" in title
    assert "font-size: var(--type-kicker, 1.6rem)" in title


def test_supplied_tokens_are_emitted_as_css_custom_properties() -> None:
    html = build_slide_html(
        layout_type="scorecard",
        content={"title": "S", "metrics": [{"label": "L", "value": "1"}]},
        brand=_BRAND,
        tokens=_TOKENS,
    )
    # snake_case -> kebab-case, one declaration per token, inside :root.
    assert "--surface: #FFFFFF;" in html
    assert "--surface-2: #F4F6F8;" in html
    assert "--on-surface: #101418;" in html
    assert "--on-surface-muted: #5A6472;" in html
    assert "--accent-2: #7C5CFF;" in html
    assert "--accent-soft: rgba(124, 92, 255, 0.12);" in html
    assert "--type-h1: clamp(2.2rem, 3.8vw, 3.6rem);" in html
    assert "--type-body: 1.8rem;" in html
    assert "--grid-gutter: 32px;" in html
    assert "--grid-margin: 96px;" in html
    assert "--type-scale-ratio: 1.25;" in html
    # The 5 pre-existing brand vars still render — nothing regressed.
    assert "--primary: #0B1F3A;" in html
    assert "--secondary: #2BB6A3;" in html
    assert "--accent: #F26B3A;" in html
    assert '--font-display: "Inter"' in html
    assert "--font: var(--font-body);" in html


def test_brand_accent_wins_the_cascade_over_the_colliding_design_token() -> None:
    """The token map's `accent` kebab-cases onto the same `--accent` the brand
    channel declares, and a later declaration of the same custom property wins.

    The brand channel must win: apply_design_tokens() folds the design system
    into it with a deliberate mapping (accent_color = palette.accent_2, NOT
    palette.accent), and consumers like cta's accent-circle use a bare
    var(--accent) with no fallback. If the token loop is ever moved back below
    the brand vars, the accent-circle silently renders palette.accent instead.

    Asserting presence is not enough — an overridden declaration is still
    "in html". Pin the ORDER.
    """
    html = build_slide_html(
        layout_type="cta",
        content={"title": "C", "cta_text": "Go"},
        brand=_BRAND,
        tokens=_TOKENS,
    )
    token_accent = html.index("--accent: #00FF00;")  # from the token map
    brand_accent = html.index("--accent: #F26B3A;")  # from the brand channel
    assert brand_accent > token_accent, (
        "the brand --accent must be declared AFTER the design token's --accent, "
        "or the token silently overrides the accent_2 mapping"
    )


def test_unusable_tokens_are_dropped_not_sanitised() -> None:
    """A token that could break out of its declaration is dropped entirely — the
    template then falls back to its literal, which always renders."""
    html = build_slide_html(
        layout_type="scorecard",
        content={"title": "S"},
        brand=_BRAND,
        tokens={
            "surface": "#fff; } body { display: none",  # CSS injection attempt
            "bad key!": "#000",  # unusable custom-property name
            "on_surface": "  ",  # empty after strip
            "type_h1": "2rem",  # the one good token
        },
    )
    assert "display: none" not in html
    assert "--surface:" not in html
    assert "--on-surface:" not in html
    assert "bad key" not in html
    assert "--type-h1: 2rem;" in html


# --------------------------------------------------------------------------
# SLIDE-2 — new layout families (quote / stat / section_divider / timeline /
# two_up) + the orphan re-routes (agenda / roadmap / process_flow that used to
# fall back to the generic title family, dropping their list/phase/step content).
# --------------------------------------------------------------------------


def test_quote_renders_quote_and_attribution() -> None:
    html = build_slide_html(
        layout_type="quote",
        content={
            "quote": "The people closest to the work decide what is worth improving.",
            "attribution": "Jordan Rivera",
            "role": "Head of Operations, CommonPractice",
        },
        brand=_BRAND,
    )
    assert "The people closest to the work decide what is worth improving." in html
    assert "Jordan Rivera" in html
    assert "Head of Operations, CommonPractice" in html
    # Consumes the design-token surface (fallback form present regardless of tokens).
    assert "var(--type-display" in html
    assert "var(--accent" in html
    assert "…" not in html


def test_quote_alias_testimonial_uses_quote_family() -> None:
    html = build_slide_html(
        layout_type="testimonial",
        content={"quote": "A clear signal.", "attribution": "A. Person"},
        brand=_BRAND,
    )
    assert "A clear signal." in html
    assert "A. Person" in html


def test_stat_renders_hero_value_and_label() -> None:
    html = build_slide_html(
        layout_type="stat",
        content={"value": "94%", "label": "faster onboarding", "caption": "vs. the manual flow"},
        brand=_BRAND,
    )
    assert "94%" in html
    assert "faster onboarding" in html
    assert "vs. the manual flow" in html
    assert "var(--type-display" in html


def test_section_divider_renders_items_list() -> None:
    html = build_slide_html(
        layout_type="section_divider",
        content={
            "eyebrow": "Chapter 2",
            "title": "Where the time goes",
            "items": [{"text": "Intake"}, {"text": "Triage"}, ""],  # empty dropped
        },
        brand=_BRAND,
    )
    assert "Chapter 2" in html
    assert "Where the time goes" in html
    assert '<ol class="items"' in html
    assert "Intake" in html
    assert "Triage" in html
    assert html.count("<li>") == 2  # empty item dropped


def test_timeline_renders_beats_from_phases() -> None:
    """The timeline family reads roadmap-shaped `phases` ({name, description, timeline})."""
    html = build_slide_html(
        layout_type="timeline",
        content={
            "title": "Rollout",
            "phases": [
                {"name": "Pilot", "description": "One team", "timeline": "Q1"},
                {"name": "Scale", "description": "All teams", "timeline": "Q2"},
            ],
        },
        brand=_BRAND,
    )
    assert '<div class="timeline">' in html
    assert "Pilot" in html
    assert "Scale" in html
    assert "Q1" in html  # timeline value used as the beat marker
    assert "Q2" in html


def test_timeline_marker_falls_back_to_ordinal() -> None:
    """process_flow-shaped `steps` ({label, description}) carry no marker → ordinal badge."""
    html = build_slide_html(
        layout_type="timeline",
        content={"steps": [{"label": "Draft"}, {"label": "Review"}]},
        brand=_BRAND,
    )
    assert "Draft" in html
    assert "Review" in html
    assert "01" in html  # zero-padded ordinal marker
    assert "02" in html


def test_two_up_renders_both_columns() -> None:
    html = build_slide_html(
        layout_type="two_up",
        content={
            "title": "Before vs after",
            "left": {"heading": "Manual", "body": "Slow", "bullets": ["forms", "email"]},
            "right": {"heading": "Automated", "body": "Fast", "bullets": ["one click"]},
        },
        brand=_BRAND,
    )
    assert "Manual" in html
    assert "Automated" in html
    assert "forms" in html
    assert "one click" in html
    assert 'class="col left"' in html
    assert 'class="col right"' in html


def test_two_up_accepts_flat_column_keys() -> None:
    html = build_slide_html(
        layout_type="split",  # alias
        content={"left_heading": "A", "left_body": "aaa", "right_heading": "B"},
        brand=_BRAND,
    )
    assert "A" in html
    assert "aaa" in html
    assert "B" in html


# -- Orphan re-routes: already-emitted layout_types now render richer -------


def test_agenda_reroutes_to_section_divider_not_title() -> None:
    """`agenda` used to fall back to the title family (dropping its `items`). It now
    renders the section_divider family, so its list survives."""
    html = build_slide_html(
        layout_type="agenda",
        content={"title": "Today", "items": [{"text": "Intro"}, {"text": "Deep dive"}]},
        brand=_BRAND,
    )
    assert '<ol class="items"' in html  # section_divider marker
    assert "Intro" in html
    assert "Deep dive" in html
    assert 'class="accent-bar"' not in html  # NOT the title family


def test_roadmap_reroutes_to_timeline_not_title() -> None:
    html = build_slide_html(
        layout_type="roadmap",
        content={"title": "Plan", "phases": [{"name": "Alpha", "timeline": "H1"}]},
        brand=_BRAND,
    )
    assert '<div class="timeline">' in html  # timeline marker
    assert "Alpha" in html
    assert "H1" in html
    assert 'class="accent-bar"' not in html  # NOT the title family


def test_process_flow_reroutes_to_timeline_not_title() -> None:
    html = build_slide_html(
        layout_type="process_flow",
        content={"title": "How it works", "steps": [{"label": "Ingest"}, {"label": "Render"}]},
        brand=_BRAND,
    )
    assert '<div class="timeline">' in html
    assert "Ingest" in html
    assert "Render" in html


def test_title_family_unchanged_after_reroutes() -> None:
    """Byte-identity guard: `title` itself still renders the title family (accent-bar),
    proving the re-routes only moved the orphan layout_types, not `title`."""
    html = build_slide_html(
        layout_type="title",
        content={"eyebrow": "Deck", "title": "Hello", "subtitle": "World"},
        brand=_BRAND,
    )
    assert 'class="accent-bar"' in html
    assert "Hello" in html


# --------------------------------------------------------------------------
# Real Chromium — local / in-image only
# --------------------------------------------------------------------------

_needs_chromium = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright chromium not installed"
)


@_needs_chromium
async def test_render_slide_png_real_chromium() -> None:
    """End-to-end render via real Chromium.

    All assertions live in ONE test on purpose: the browser pool is a module
    global and pytest-asyncio gives each test its own event loop, so reusing the
    pool across tests would block on a queue bound to a closed loop. One test ==
    one loop == one pool, torn down explicitly at the end.
    """
    from src.services.browser_pool import close_browser_pool

    try:
        # 16:9 → 1920x1080 CSS @ the RENDER_DEVICE_SCALE_FACTOR default of 2.0
        # → 3840x2160 output pixels. These assert OUTPUT pixels, so they are the
        # end-to-end proof that the shipped default actually reaches Chromium:
        # the viewport stays 1920x1080 CSS (layout/wrapping unchanged) and only
        # the bitmap grows. The compiler's ken_burns zoom crops *into* this PNG,
        # so a regression of the default back to 1.0 lands here as (1920, 1080).
        png_16_9 = await render_slide_png(
            layout_type="title",
            content={"eyebrow": "Common Practice", "title": _LONG_TITLE},
            brand=_BRAND,
            aspect_ratio="16:9",
        )
        assert _png_size(png_16_9) == (3840, 2160)

        # 4:3 → 1440x1080 CSS @ 2.0 → 2880x2160.
        png_4_3 = await render_slide_png(
            layout_type="title",
            content={"title": "Hi"},
            brand=_BRAND,
            aspect_ratio="4:3",
        )
        assert _png_size(png_4_3) == (2880, 2160)

        # CTA with a long URL: full-bleed, non-trivial (not a blank frame).
        png_cta = await render_slide_png(
            layout_type="cta",
            content={"eyebrow": "Common Practice", "title": "Start today", "cta_url": _LONG_URL},
            brand=_BRAND,
        )
        assert _png_size(png_cta) == (3840, 2160)
        assert len(png_cta) > 5000

        # An explicit per-request scale must still WIN over the service default —
        # that is the contract `render_scale` in the deck payload relies on. Ask
        # for 1.0 and get exactly 1920x1080 back even though the default is 2.0.
        png_1x = await render_slide_png(
            layout_type="title",
            content={"eyebrow": "Common Practice", "title": _LONG_TITLE},
            brand=_BRAND,
            aspect_ratio="16:9",
            scale=1.0,
        )
        assert _png_size(png_1x) == (1920, 1080)

        # ...and an explicit 2.0 is identical to the default, confirming the
        # default is genuinely 4K rather than the override being the only 4K path.
        png_4k = await render_slide_png(
            layout_type="title",
            content={"eyebrow": "Common Practice", "title": _LONG_TITLE},
            brand=_BRAND,
            aspect_ratio="16:9",
            scale=2.0,
        )
        assert _png_size(png_4k) == (3840, 2160)
    finally:
        await close_browser_pool()


# --------------------------------------------------------------------------
# Shipped default reaches Chromium — runs WITHOUT chromium installed
# --------------------------------------------------------------------------


class _ScaleCapturingPage:
    """Minimal Playwright ``Page`` stand-in that records nothing but succeeds."""

    async def set_content(self, _html: str, **_kw: object) -> None:
        return None

    async def evaluate(self, _expr: str) -> None:
        return None

    async def screenshot(self, **_kw: object) -> bytes:
        return b"\x89PNG\r\n\x1a\n"

    async def close(self) -> None:
        return None


class _ScaleCapturingBrowser:
    """Captures the kwargs ``render_slide_png`` hands to ``browser.new_page``."""

    def __init__(self, sink: list[dict[str, object]]) -> None:
        self._sink = sink

    async def new_page(self, **kwargs: object) -> _ScaleCapturingPage:
        self._sink.append(kwargs)
        return _ScaleCapturingPage()


class _ScaleCapturingPool:
    def __init__(self, sink: list[dict[str, object]]) -> None:
        self._sink = sink

    @contextlib.asynccontextmanager
    async def lease(self):  # type: ignore[no-untyped-def]
        yield _ScaleCapturingBrowser(self._sink)


async def _capture_new_page_kwargs(scale: float | None = None) -> dict[str, object]:
    """Run a render against a fake pool and return the ``new_page`` kwargs.

    ``scale=None`` is the production shape: the renderer falls back to the
    ``RENDER_DEVICE_SCALE_FACTOR`` service default.
    """
    sink: list[dict[str, object]] = []
    with patch(
        "src.services.html_slide_renderer.get_browser_pool",
        new=AsyncMock(return_value=_ScaleCapturingPool(sink)),
    ):
        await render_slide_png(
            layout_type="title",
            content={"title": "Hi"},
            brand=_BRAND,
            scale=scale,
        )
    assert len(sink) == 1, f"expected exactly one new_page call, got {len(sink)}"
    return sink[0]


async def test_shipped_default_scale_reaches_the_playwright_context() -> None:
    """The DEFAULT ``RENDER_DEVICE_SCALE_FACTOR`` must arrive at Chromium as 2.0.

    This deliberately reads the real ``Settings`` (no ``SimpleNamespace`` stub) and
    asserts the value handed to ``browser.new_page``, because a settings readback
    proves only that the field holds 2.0 — not that the render path consumes it.
    Slides used to render at 1.0 and the video compiler's ken_burns zoom then
    cropped *into* that 1920x1080 bitmap, shipping a sub-1080p, soft frame.

    It is the CI-visible twin of the real-Chromium pixel assertions above, which
    skip wherever ``playwright install chromium`` has not been run.
    """
    kwargs = await _capture_new_page_kwargs()

    assert kwargs["device_scale_factor"] == 2.0
    # The CSS viewport must NOT have grown — scale changes output pixels only, so
    # layout, wrapping and text balance stay byte-identical to the 1.0 renders.
    assert kwargs["viewport"] == {"width": 1920, "height": 1080}


async def test_per_request_scale_still_overrides_the_new_default() -> None:
    """An explicit ``scale`` wins over the default, in BOTH directions.

    ``deck_builder`` forwards the request payload's ``render_scale`` as ``scale``,
    so raising the default must not swallow a caller that deliberately asks for
    something cheaper (or dearer) than 4K.
    """
    assert (await _capture_new_page_kwargs(scale=1.0))["device_scale_factor"] == 1.0
    assert (await _capture_new_page_kwargs(scale=1.333))["device_scale_factor"] == 1.333
    assert (await _capture_new_page_kwargs(scale=4.0))["device_scale_factor"] == 4.0


@pytest.mark.skipif(not _chromium_available(), reason="Chromium must be installed")
@pytest.mark.asyncio
async def test_title_cards_measure_and_fit_copy_instead_of_silently_clipping():
    from playwright.async_api import async_playwright

    from src.services.html_slide_renderer import _FIT_TITLE_TEXT

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        try:
            for words, expected in [(69, True), (4000, False)]:
                html = build_slide_html(
                    layout_type="title", content={"title": "knowledge work " * words}, brand=_BRAND
                )
                await page.set_content(html)
                await page.evaluate("document.fonts.ready")
                assert await page.evaluate(_FIT_TITLE_TEXT) is expected
        finally:
            await browser.close()
