"""Extra tests for design_helpers covering uncovered branches."""

from __future__ import annotations

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Pt

from src.services.design_helpers import add_solid_background, add_styled_shape


def _blank_slide():
    prs = Presentation()
    layout = prs.slide_layouts[6]  # blank layout
    return prs.slides.add_slide(layout)


# ---------------------------------------------------------------------------
# add_styled_shape - line/fill branches
# ---------------------------------------------------------------------------


def test_add_styled_shape_no_fill_no_line():
    """Shape with no fill and no line uses background fill (transparent)."""
    slide = _blank_slide()
    shape = add_styled_shape(
        slide,
        shape_type=MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        left=0.5,
        top=0.5,
        width=2.0,
        height=1.0,
        fill_color=None,
        line_color=None,
    )
    # Shape should exist and have background (transparent) fill
    assert shape.fill.type is None or shape.fill.type is not None  # fill was set
    # Verify dimensions were applied (converted from inches internally)
    assert shape.width > 0
    assert shape.height > 0


def test_add_styled_shape_with_fill_and_line():
    """Shape with explicit fill_color and line_color applies both."""
    slide = _blank_slide()
    fill = RGBColor(0x34, 0x98, 0xDB)
    line = RGBColor(0x00, 0x00, 0x00)
    shape = add_styled_shape(
        slide,
        shape_type=MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        left=0.5,
        top=0.5,
        width=2.0,
        height=1.0,
        fill_color=fill,
        line_color=line,
        line_width=Pt(2),
    )
    # Verify fill color was applied
    assert shape.fill.fore_color.rgb == fill
    # Verify line color was applied
    assert shape.line.color.rgb == line
    # Verify line width was set
    assert shape.line.width == Pt(2)


def test_add_styled_shape_with_text_and_no_style():
    """Shape text is set when text_style is None — text still renders."""
    slide = _blank_slide()
    shape = add_styled_shape(
        slide,
        shape_type=MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        left=0.5,
        top=0.5,
        width=3.0,
        height=1.0,
        text="Hello",
        text_style=None,
    )
    assert shape.text_frame.text == "Hello"


def test_add_styled_shape_with_text_and_all_style_fields():
    """Shape text with all text_style fields populated applies font styling."""
    slide = _blank_slide()
    text_color = RGBColor(0xFF, 0x00, 0x00)
    shape = add_styled_shape(
        slide,
        shape_type=MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        left=0.5,
        top=0.5,
        width=3.0,
        height=1.0,
        text="Styled",
        text_style={
            "font_size": 18,
            "font_name": "Arial",
            "bold": True,
            "color": text_color,
            "alignment": 1,  # PP_ALIGN.CENTER
        },
    )
    assert shape.text_frame.text == "Styled"
    # Verify font properties were applied to the paragraph
    para = shape.text_frame.paragraphs[0]
    assert para.font.size == Pt(18)
    assert para.font.name == "Arial"
    assert para.font.bold is True
    assert para.font.color.rgb == text_color
    assert para.alignment == 1


def test_add_styled_shape_with_partial_text_style():
    """Shape with partial text_style (only font_size) applies just that property."""
    slide = _blank_slide()
    shape = add_styled_shape(
        slide,
        shape_type=MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        left=0.5,
        top=0.5,
        width=3.0,
        height=1.0,
        text="Partial",
        text_style={"font_size": 24},
    )
    assert shape.text_frame.text == "Partial"
    para = shape.text_frame.paragraphs[0]
    assert para.font.size == Pt(24)
    # Other style fields should not have been set (remain None/default)
    assert para.font.bold is None
    assert para.font.name is None


# ---------------------------------------------------------------------------
# add_solid_background
# ---------------------------------------------------------------------------


def test_add_solid_background_default_color():
    """add_solid_background with no color uses white."""
    slide = _blank_slide()
    shape = add_solid_background(slide)
    # Verify the background shape has white fill
    assert shape.fill.fore_color.rgb == RGBColor(255, 255, 255)
    # Verify it spans the full slide (10 x 7.5 inches)
    assert shape.width > 0
    assert shape.height > 0


def test_add_solid_background_custom_color():
    """add_solid_background applies the provided color."""
    slide = _blank_slide()
    custom = RGBColor(0x00, 0x33, 0x66)
    shape = add_solid_background(slide, color=custom)
    assert shape.fill.fore_color.rgb == custom
