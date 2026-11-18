"""Tests for template renderer dispatch."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from src.services.template_renderer import render_slide


@pytest.fixture
def prs():
    return Presentation()


@pytest.fixture
def brand():
    return {
        "primary_color": "#1a73e8",
        "secondary_color": "#34a853",
        "font_family": "Arial",
        "theme": "modern",
    }


@pytest.fixture
def styling_rules():
    return {"title_font_size": 28, "layout": "centered"}


@pytest.fixture
def sample_image_data_url() -> str:
    """Create an in-memory PNG data URL for deterministic image rendering tests."""
    image = Image.new("RGB", (320, 180), color=(46, 125, 50))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@pytest.fixture
def sample_tall_image_data_url() -> str:
    """Create a tall in-memory PNG data URL for contain-fit tests."""
    image = Image.new("RGB", (180, 320), color=(189, 30, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _text_shapes(slide):
    """Extract all text content from shapes on a slide."""
    texts = []
    for shape in slide.shapes:
        if hasattr(shape, "text_frame"):
            text = shape.text_frame.text.strip()
            if text:
                texts.append(text)
    return texts


def test_render_title_slide(prs, brand, styling_rules):
    """Title slide renders with correct title text and one slide added."""
    slide = render_slide(
        prs,
        layout_type="title",
        content={"title": "Hello World", "subtitle": "Testing"},
        brand=brand,
        styling_rules=styling_rules,
    )
    assert len(prs.slides) == 1
    texts = _text_shapes(slide)
    assert any("Hello World" in t for t in texts)
    assert any("Testing" in t for t in texts)


def test_render_agenda_slide(prs, brand, styling_rules):
    """Agenda slide renders title and all agenda items."""
    slide = render_slide(
        prs,
        layout_type="agenda",
        content={"title": "Agenda", "items": ["Item 1", "Item 2"]},
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    assert any("Agenda" in t for t in texts)
    assert any("Item 1" in t for t in texts)
    assert any("Item 2" in t for t in texts)


def test_render_scorecard_slide(prs, brand, styling_rules):
    """Scorecard slide renders metric labels and values."""
    slide = render_slide(
        prs,
        layout_type="scorecard",
        content={
            "title": "Scores",
            "metrics": [
                {"label": "Speed", "value": "95"},
                {"label": "Quality", "value": "88"},
            ],
        },
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    assert any("Scores" in t for t in texts)
    assert any("Speed" in t for t in texts)
    assert any("95" in t for t in texts)
    assert any("Quality" in t for t in texts)
    assert any("88" in t for t in texts)


def test_render_cta_slide(prs, brand, styling_rules):
    """CTA slide renders title, body, and button text."""
    slide = render_slide(
        prs,
        layout_type="cta",
        content={
            "title": "Get Started",
            "body": "Contact us today",
            "cta_text": "Sign Up",
        },
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    assert any("Get Started" in t for t in texts)
    assert any("Contact us today" in t for t in texts)
    assert any("Sign Up" in t for t in texts)


def test_render_process_flow_slide(prs, brand, styling_rules):
    """Process flow slide renders all step labels."""
    slide = render_slide(
        prs,
        layout_type="process_flow",
        content={
            "title": "Our Process",
            "steps": [
                {"label": "Plan", "description": "Define goals"},
                {"label": "Build", "description": "Implement"},
                {"label": "Ship", "description": "Deploy"},
            ],
        },
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    assert any("Our Process" in t for t in texts)
    assert any("Plan" in t for t in texts)
    assert any("Build" in t for t in texts)
    assert any("Ship" in t for t in texts)


def test_render_unknown_layout(prs, brand, styling_rules):
    """Test that unknown layout raises ValueError."""
    with pytest.raises(ValueError, match="Unknown layout_type"):
        render_slide(
            prs,
            layout_type="nonexistent",
            content={},
            brand=brand,
            styling_rules=styling_rules,
        )


def test_render_all_layouts(prs, brand, styling_rules):
    """All 11 layout types render without errors and produce correct slide count."""
    layouts_and_content = [
        ("title", {"title": "Title", "subtitle": "Sub"}),
        ("agenda", {"title": "Agenda", "items": ["A", "B"]}),
        ("scorecard", {"title": "Scores", "metrics": [{"label": "X", "value": "1"}]}),
        ("finding_detail", {"title": "Finding", "description": "Desc"}),
        ("comparison", {"title": "Compare", "before_label": "Before", "after_label": "After"}),
        ("feature_spotlight", {"title": "Feature", "description": "Desc"}),
        ("process_flow", {"title": "Flow", "steps": [{"label": "S1"}]}),
        ("metric_summary", {"title": "KPIs", "kpis": [{"label": "K", "value": "1"}]}),
        ("roadmap", {"title": "Roadmap", "phases": [{"name": "P1"}]}),
        ("cta", {"title": "CTA", "body": "Do it", "cta_text": "Go"}),
        ("tutorial_step", {"step_number": "1", "title": "Step", "instruction": "Do this"}),
    ]

    for layout_type, content in layouts_and_content:
        slide = render_slide(prs, layout_type, content, brand, styling_rules)
        # Each slide should have at least one shape (all renderers add shapes)
        assert len(slide.shapes) > 0, f"Layout '{layout_type}' produced no shapes"
        # Title text should appear in each slide's shapes
        texts = _text_shapes(slide)
        expected_title = content.get("title", content.get("step_number", ""))
        if expected_title:
            assert any(expected_title in t for t in texts), (
                f"Title '{expected_title}' not found in {layout_type} slide"
            )

    assert len(prs.slides) == 11


def test_render_slide2_layouts(prs, brand, styling_rules):
    """The SLIDE-2 families (quote/stat/section_divider/timeline/two_up) each render
    a PPTX slide with shapes. deck_builder renders BOTH the PPTX and the PNG per
    slide, so a missing/ raising PPTX renderer would crash the whole deck build."""
    layouts_and_content = [
        ("quote", {"quote": "Closest to the work.", "attribution": "J. Rivera"}),
        ("stat", {"value": "94%", "label": "faster", "caption": "vs manual"}),
        ("section_divider", {"eyebrow": "Ch 2", "title": "Time", "items": [{"text": "Intake"}]}),
        ("timeline", {"title": "Plan", "phases": [{"name": "Pilot", "timeline": "Q1"}]}),
        ("two_up", {"title": "A vs B", "left": {"heading": "A"}, "right": {"heading": "B"}}),
    ]
    for layout_type, content in layouts_and_content:
        slide = render_slide(prs, layout_type, content, brand, styling_rules)
        assert len(slide.shapes) > 0, f"Layout '{layout_type}' produced no shapes"


def test_pptx_renderer_accepts_html_layout_aliases(prs, brand, styling_rules):
    """Regression: the PPTX ``_RENDERERS`` map must accept the SAME alias vocabulary
    as html_slide_renderer (``testimonial``->quote, ``metric_hero``->stat,
    ``split``->two_up). deck_builder renders the PPTX FIRST and hard-raises
    ``ValueError: Unknown layout_type`` on a missing key, which fails the WHOLE deck
    build even though the HTML preview renders the alias fine — the
    `w2v-slide-layouts-need-both-renderers` class."""
    aliases_and_content = [
        ("testimonial", {"quote": "Closest to the work.", "attribution": "J. Rivera"}),
        ("metric_hero", {"value": "94%", "label": "faster", "caption": "vs manual"}),
        ("split", {"title": "A vs B", "left": {"heading": "A"}, "right": {"heading": "B"}}),
    ]
    for layout_type, content in aliases_and_content:
        # Must NOT raise ValueError; must produce a real slide with shapes.
        slide = render_slide(prs, layout_type, content, brand, styling_rules)
        assert len(slide.shapes) > 0, f"Alias '{layout_type}' produced no shapes"


def test_pptx_alias_dispatches_to_same_renderer_as_canonical(prs, brand, styling_rules):
    """Each alias must resolve to the same renderer type as its canonical layout."""
    from src.services.template_renderer import _RENDERERS

    assert type(_RENDERERS["testimonial"]) is type(_RENDERERS["quote"])
    assert type(_RENDERERS["metric_hero"]) is type(_RENDERERS["stat"])
    assert type(_RENDERERS["split"]) is type(_RENDERERS["two_up"])


def test_render_stat_with_numeric_value_does_not_crash(prs, brand, styling_rules):
    """Regression: a `stat` slide's whole point is a number; an int `value` (or
    numeric label/caption) must not blow up on len()/pptx run-text assignment."""
    slide = render_slide(
        prs,
        layout_type="stat",
        content={"value": 42, "label": 7, "caption": 3.14},
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    assert any("42" in t for t in texts)


def test_render_slide2_layouts_empty_content_no_crash(prs, brand, styling_rules):
    """Degenerate/empty content still yields a valid slide (no ValueError, ≥1 shape)."""
    for layout_type in ("quote", "stat", "section_divider", "timeline", "two_up"):
        slide = render_slide(prs, layout_type, {}, brand, styling_rules)
        assert len(slide.shapes) > 0, f"Empty '{layout_type}' produced no shapes"


def test_render_comparison_slide_embeds_picture_shapes(
    prs, brand, styling_rules, sample_image_data_url
):
    """Comparison slides should render actual picture shapes, not text placeholders."""
    slide = render_slide(
        prs,
        layout_type="comparison",
        content={
            "title": "Before and After",
            "before_label": "Before",
            "before_image_url": sample_image_data_url,
            "after_label": "After",
            "after_image_url": sample_image_data_url,
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 2
    assert all(shape.width > 0 and shape.height > 0 for shape in pictures)


def test_render_comparison_slide_supports_full_bleed_images(
    prs, brand, styling_rules, sample_image_data_url
):
    """Full-bleed comparison slides should let each image reach its slide edge."""
    slide = render_slide(
        prs,
        layout_type="comparison",
        content={
            "title": "Edge to Edge",
            "before_label": "Current",
            "before_image_url": sample_image_data_url,
            "after_label": "Target",
            "after_image_url": sample_image_data_url,
            "asset_placements": {
                "before_image_url": {"mode": "full_bleed", "fit": "cover"},
                "after_image_url": {"mode": "full_bleed", "fit": "cover"},
            },
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    pictures = sorted(
        [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE],
        key=lambda shape: shape.left,
    )
    assert len(pictures) == 2
    assert pictures[0].left == 0
    assert abs((pictures[1].left + pictures[1].width) - prs.slide_width) < 20000


def test_render_feature_spotlight_supports_full_bleed_images(
    prs, brand, styling_rules, sample_image_data_url
):
    """Feature spotlight slides should support a full-bleed hero treatment."""
    slide = render_slide(
        prs,
        layout_type="feature_spotlight",
        content={
            "title": "Spotlight",
            "description": "A richer visual treatment for deck hero assets.",
            "image_url": sample_image_data_url,
            "asset_placements": {"image_url": {"mode": "full_bleed", "fit": "cover"}},
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.left > prs.slide_width / 3
    assert abs((picture.left + picture.width) - prs.slide_width) < 20000


def test_render_tutorial_step_embeds_picture_shape(
    prs, brand, styling_rules, sample_image_data_url
):
    """Tutorial slides should render the screenshot into a picture frame."""
    slide = render_slide(
        prs,
        layout_type="tutorial_step",
        content={
            "step_number": "1",
            "title": "Open the dashboard",
            "instruction": "Click Settings and open the account dashboard to review activity.",
            "screenshot_url": sample_image_data_url,
            "tip": "Pin the dashboard for faster access.",
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.left > 0
    assert picture.top > 0


def test_render_tutorial_step_supports_contain_fit(
    prs, brand, styling_rules, sample_tall_image_data_url
):
    """Contain fit should center a tall screenshot inside the tutorial frame."""
    slide = render_slide(
        prs,
        layout_type="tutorial_step",
        content={
            "step_number": "1",
            "title": "Review the sidebar",
            "instruction": "Open the account settings drawer and review the options.",
            "screenshot_url": sample_tall_image_data_url,
            "asset_placements": {"screenshot_url": {"mode": "framed", "fit": "contain"}},
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.left > Inches(6.2)
    assert picture.height < Inches(3.8)
