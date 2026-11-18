#!/usr/bin/env python3
"""
Enhanced PowerPoint Example

This script demonstrates how to use the pptx_design_helpers to create
visually enhanced PowerPoint presentations.
"""

from pathlib import Path

# Import our design helpers
import pptx_design_helpers as design
from pptx import Presentation
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches


def create_basic_example():
    """Create a basic example presentation with design helpers."""
    # Create a presentation
    prs = Presentation()

    # Apply a theme
    theme = design.apply_theme(prs, "modern")
    palette = theme["palette"]

    # Create title slide
    design.create_title_slide(
        prs,
        title="Enhanced PowerPoint Presentation",
        subtitle="Using Design Helpers to Create Professional Slides",
        palette=palette,
    )

    # Create section slide
    design.create_section_slide(
        prs,
        title="Design Elements",
        subtitle="Showcasing Various Design Capabilities",
        palette=palette,
    )

    # Create content slide with bullet points
    bullet_points = [
        "Professional color schemes with consistent palettes",
        "Enhanced typography with proper font hierarchy",
        "Layout helpers for consistent grid-based designs",
        "Shape styling with text formatting built-in",
        "Background and content area management",
    ]

    design.create_content_slide(
        prs, title="Key Design Features", bullet_points=bullet_points, palette=palette
    )

    # Create comparison slide
    left_title = "Before Design Helpers"
    right_title = "After Design Helpers"

    left_points = [
        "Inconsistent colors across slides",
        "Manual positioning of elements",
        "Text formatting varies between shapes",
        "Complex element alignment",
        "Hard-coded style values",
    ]

    right_points = [
        "Consistent color palette throughout",
        "Grid-based layouts for alignment",
        "Standardized text styling",
        "Automatic element positioning",
        "Reusable design patterns",
    ]

    design.create_comparison_slide(
        prs,
        title="Design Comparison",
        left_title=left_title,
        right_title=right_title,
        left_points=left_points,
        right_points=right_points,
        palette=palette,
    )

    # Create flowchart slide
    elements = [
        ("Start", 1, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "primary"),
        ("Process Data", 4, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.RECTANGLE, "secondary"),
        ("Decision", 4, 3.5, 2, 0.75, MSO_AUTO_SHAPE_TYPE.DIAMOND, "accent1"),
        ("Output", 7, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "accent2"),
    ]

    connections = [(0, 1, "Input"), (1, 2, "Analyze"), (2, 3, "Results")]

    design.create_flowchart_slide(
        prs,
        title="Process Flow Example",
        elements=elements,
        connections=connections,
        palette=palette,
    )

    # Create a custom slide using the helpers directly
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank layout

    # Add title with styling
    design.create_title(slide, "Custom Design Elements", font_size=36, color=palette.primary)

    # Add a colored background area
    design.add_solid_background(
        slide,
        color=design.hex_to_rgb("#f5f5f5"),  # Light gray
    )

    # Create a 2x2 grid layout
    grid = design.create_grid_layout(slide, 2, 2)

    # Add various styled shapes to each grid cell
    cell = grid[0][0]  # Top-left cell
    shape1 = design.add_styled_rectangle(
        slide,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.primary,
        line_color=None,
        text="Styled Rectangle",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    cell = grid[0][1]  # Top-right cell
    shape2 = design.add_styled_oval(
        slide,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.secondary,
        line_color=None,
        text="Styled Oval",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    cell = grid[1][0]  # Bottom-left cell
    shape3 = design.add_styled_rounded_rectangle(
        slide,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.accent1,
        line_color=None,
        text="Rounded Rectangle",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    cell = grid[1][1]  # Bottom-right cell
    shape4 = design.add_styled_shape(
        slide,
        MSO_AUTO_SHAPE_TYPE.DIAMOND,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.accent2,
        line_color=None,
        text="Diamond Shape",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    # Add text arrow connections (instead of connectors for better compatibility)
    design.add_arrow(
        slide,
        shape1.left + shape1.width,
        shape1.top + shape1.height / 2,
        shape2.left,
        shape2.top + shape2.height / 2,
        line_color=palette.dark,
    )

    design.add_arrow(
        slide,
        shape1.left + shape1.width / 2,
        shape1.top + shape1.height,
        shape3.left + shape3.width / 2,
        shape3.top,
        line_color=palette.dark,
    )

    # Add a text box with animation notes
    design.add_text_box(
        slide,
        "Animation hints are added via alt text",
        left=1,
        top=6,
        width=8,
        height=0.5,
        font_size=12,
        italic=True,
        color=palette.dark,
        alignment=design.PP_ALIGN.CENTER,
    )

    # Add animation notes to shapes
    design.add_animation_notes_to_shape(shape1, "fade", 1, 0.5, 0)
    design.add_animation_notes_to_shape(shape2, "fly_in", 2, 0.5, 0.2)
    design.add_animation_notes_to_shape(shape3, "wipe", 3, 0.5, 0.4)
    design.add_animation_notes_to_shape(shape4, "zoom", 4, 0.5, 0.6)

    # Create conclusion slide
    conclusion_slide = prs.slides.add_slide(prs.slide_layouts[5])

    # Add a different background for the conclusion
    design.add_solid_background(conclusion_slide, palette.dark)

    # Add title
    design.add_text_box(
        conclusion_slide,
        "Thank You!",
        left=1.5,
        top=2.5,
        width=7,
        height=1,
        font_size=48,
        font_name="Segoe UI Light",
        bold=False,
        color=design.RGBColor(255, 255, 255),
        alignment=design.PP_ALIGN.CENTER,
    )

    # Add subtitle
    design.add_text_box(
        conclusion_slide,
        "For more information on design helpers, check the documentation",
        left=2,
        top=4,
        width=6,
        height=0.5,
        font_size=18,
        font_name="Segoe UI",
        color=design.RGBColor(200, 200, 200),
        alignment=design.PP_ALIGN.CENTER,
    )

    # Save the presentation
    output_folder = Path("output_slides")
    output_folder.mkdir(exist_ok=True)
    prs.save(str(output_folder / "enhanced_presentation_example.pptx"))
    print(f"Enhanced presentation saved to {output_folder}/enhanced_presentation_example.pptx")

    return prs


if __name__ == "__main__":
    create_basic_example()
