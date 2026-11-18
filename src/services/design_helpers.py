"""PPTX design helpers -- refactored from pptx_design_helpers.py."""

from __future__ import annotations

import logging
from typing import Any

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_VERTICAL_ANCHOR, PP_ALIGN, PP_PARAGRAPH_ALIGNMENT
from pptx.slide import Slide
from pptx.text.text import TextFrame
from pptx.util import Inches, Pt

logger = logging.getLogger(__name__)

_DEFAULT_LINE_WIDTH = Pt(1)


def hex_to_rgb(hex_color: str) -> RGBColor:
    """Convert hex color string to RGBColor."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return RGBColor(r, g, b)


def lighten_color(color: RGBColor, factor: float = 0.5) -> RGBColor:
    """Lighten a color by blending with white."""
    r = int(color[0] + (255 - color[0]) * factor)
    g = int(color[1] + (255 - color[1]) * factor)
    b = int(color[2] + (255 - color[2]) * factor)
    return RGBColor(r, g, b)


def darken_color(color: RGBColor, factor: float = 0.5) -> RGBColor:
    """Darken a color by blending with black."""
    r = int(color[0] * (1 - factor))
    g = int(color[1] * (1 - factor))
    b = int(color[2] * (1 - factor))
    return RGBColor(r, g, b)


def apply_text_style(
    text_frame: TextFrame,
    text: str | None = None,
    font_size: int | None = None,
    font_name: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    color: RGBColor | None = None,
    alignment: PP_PARAGRAPH_ALIGNMENT | None = None,
) -> None:
    """Apply consistent text styling to a text frame."""
    if text is not None:
        text_frame.text = text

    for paragraph in text_frame.paragraphs:
        if alignment is not None:
            paragraph.alignment = alignment
        for run in paragraph.runs:
            if font_size is not None:
                run.font.size = Pt(font_size)
            if font_name is not None:
                run.font.name = font_name
            if bold is not None:
                run.font.bold = bold
            if italic is not None:
                run.font.italic = italic
            if color is not None:
                run.font.color.rgb = color


def add_text_box(
    slide: Slide,
    text: str,
    left: float,
    top: float,
    width: float,
    height: float,
    font_size: int = 12,
    font_name: str = "Arial",
    bold: bool = False,
    italic: bool = False,
    color: RGBColor | None = None,
    alignment: PP_PARAGRAPH_ALIGNMENT = PP_ALIGN.LEFT,
    vertical_alignment: int = MSO_VERTICAL_ANCHOR.TOP,
) -> Any:
    """Add a text box with consistent styling."""
    text_box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    text_box.text_frame.vertical_anchor = vertical_alignment
    apply_text_style(
        text_box.text_frame,
        text=text,
        font_size=font_size,
        font_name=font_name,
        bold=bold,
        italic=italic,
        color=color or RGBColor(0, 0, 0),
        alignment=alignment,
    )
    return text_box


def add_styled_shape(
    slide: Slide,
    shape_type: MSO_AUTO_SHAPE_TYPE,
    left: float,
    top: float,
    width: float,
    height: float,
    fill_color: RGBColor | None = None,
    line_color: RGBColor | None = None,
    line_width: Any = _DEFAULT_LINE_WIDTH,
    text: str | None = None,
    text_style: dict[str, Any] | None = None,
) -> Any:
    """Add a shape with consistent styling and optional text."""
    shape = slide.shapes.add_shape(
        shape_type, Inches(left), Inches(top), Inches(width), Inches(height)
    )

    if fill_color:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_color
    else:
        shape.fill.background()

    if line_color:
        shape.line.color.rgb = line_color
        shape.line.width = line_width
    else:
        shape.line.fill.background()

    if text:
        shape.text_frame.text = text
        if text_style:
            para = shape.text_frame.paragraphs[0]
            if "font_size" in text_style:
                para.font.size = Pt(text_style["font_size"])
            if "font_name" in text_style:
                para.font.name = text_style["font_name"]
            if "bold" in text_style:
                para.font.bold = text_style["bold"]
            if "color" in text_style:
                para.font.color.rgb = text_style["color"]
            if "alignment" in text_style:
                para.alignment = text_style["alignment"]

    return shape


def add_solid_background(slide: Slide, color: RGBColor | None = None) -> Any:
    """Add a solid color background to a slide."""
    if color is None:
        color = RGBColor(255, 255, 255)

    slide_width = Inches(10)
    slide_height = Inches(7.5)

    background = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0), Inches(0), slide_width, slide_height
    )
    background.fill.solid()
    background.fill.fore_color.rgb = color
    background.line.fill.background()

    try:
        slide.shapes._spTree.remove(background._element)
        slide.shapes._spTree.insert(2, background._element)
    except OSError, RuntimeError:
        logger.debug("Could not reorder background shape element, using default position")

    return background


def create_grid_layout(
    slide: Slide,
    rows: int,
    cols: int,
    margin: float = 0.5,
    title_height: float = 0,
) -> list[list[tuple]]:
    """Create a grid layout on a slide and return grid cell positions."""
    slide_width = Inches(10)
    slide_height = Inches(7.5)

    margin_left = margin
    margin_top = margin + title_height
    margin_right = margin
    margin_bottom = margin

    usable_width = slide_width - Inches(margin_left + margin_right)
    usable_height = slide_height - Inches(margin_top + margin_bottom)

    cell_width = usable_width / cols
    cell_height = usable_height / rows

    grid = []
    for row in range(rows):
        grid_row = []
        for col in range(cols):
            cell_left = Inches(margin_left) + (cell_width * col)
            cell_top = Inches(margin_top) + (cell_height * row)
            grid_row.append((cell_left, cell_top, cell_width, cell_height))
        grid.append(grid_row)

    return grid


THEME_PALETTES = {
    "modern": {
        "primary": "#3498db",
        "secondary": "#2ecc71",
        "accent": "#e74c3c",
        "dark": "#2c3e50",
        "light": "#ecf0f1",
        "background": "#ffffff",
    },
    "corporate": {
        "primary": "#003366",
        "secondary": "#336699",
        "accent": "#ff9900",
        "dark": "#333333",
        "light": "#f2f2f2",
        "background": "#ffffff",
    },
    "minimalist": {
        "primary": "#34495e",
        "secondary": "#7f8c8d",
        "accent": "#e74c3c",
        "dark": "#2c3e50",
        "light": "#ecf0f1",
        "background": "#ffffff",
    },
}
