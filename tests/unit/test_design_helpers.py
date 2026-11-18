"""Tests for design helpers."""

from __future__ import annotations

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from src.services.design_helpers import (
    add_solid_background,
    add_styled_shape,
    add_text_box,
    apply_text_style,
    create_grid_layout,
    darken_color,
    hex_to_rgb,
    lighten_color,
)


def test_hex_to_rgb():
    """Test hex to RGB conversion."""
    color = hex_to_rgb("#FF0000")
    assert color == RGBColor(255, 0, 0)

    color = hex_to_rgb("00FF00")
    assert color == RGBColor(0, 255, 0)

    color = hex_to_rgb("#1a73e8")
    assert color == RGBColor(26, 115, 232)


def test_lighten_color():
    """Test color lightening."""
    color = RGBColor(100, 100, 100)
    lighter = lighten_color(color, 0.5)
    assert lighter[0] > 100
    assert lighter[1] > 100
    assert lighter[2] > 100


def test_darken_color():
    """Test color darkening."""
    color = RGBColor(200, 200, 200)
    darker = darken_color(color, 0.5)
    assert darker[0] < 200
    assert darker[1] < 200
    assert darker[2] < 200


def test_apply_text_style():
    """Test applying text style to a text frame."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))

    apply_text_style(
        textbox.text_frame,
        text="Hello World",
        font_size=24,
        font_name="Arial",
        bold=True,
        alignment=PP_ALIGN.CENTER,
    )

    assert textbox.text_frame.text == "Hello World"


def test_add_text_box():
    """Test adding a styled text box."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])

    tb = add_text_box(
        slide,
        "Test Text",
        left=1,
        top=1,
        width=3,
        height=1,
        font_size=18,
        bold=True,
    )

    assert tb.text_frame.text == "Test Text"


def test_add_styled_shape():
    """Test adding a styled shape."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])

    shape = add_styled_shape(
        slide,
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        left=2,
        top=2,
        width=4,
        height=2,
        fill_color=RGBColor(0, 255, 0),
        text="Test Shape",
    )

    assert shape.text_frame.text == "Test Shape"


def test_add_solid_background():
    """Test adding a solid background."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])

    bg = add_solid_background(slide, color=RGBColor(200, 200, 200))
    assert bg.fill.fore_color.rgb == RGBColor(200, 200, 200)


def test_create_grid_layout():
    """Test creating a grid layout."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])

    grid = create_grid_layout(slide, 2, 3)

    assert len(grid) == 2
    assert len(grid[0]) == 3
    assert len(grid[1]) == 3

    for row in grid:
        for cell in row:
            assert len(cell) == 4
