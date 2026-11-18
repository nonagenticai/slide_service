#!/usr/bin/env python3
"""
Enhanced Animated PowerPoint Example

This script demonstrates how to use both the pptx_design_helpers and
animation_transition_helpers together to create visually appealing
PowerPoint presentations with animation guidance.
"""

from pathlib import Path

import animation_transition_helpers as anim

# Import our helpers
import pptx_design_helpers as design
from pptx import Presentation
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches


def create_enhanced_animated_presentation():
    """Create an enhanced presentation with animation metadata."""
    # Create a presentation
    prs = Presentation()

    # Apply a modern theme
    theme = design.apply_theme(prs, "modern")
    palette = theme["palette"]

    # Title Slide
    slide_index = 0
    slide = design.create_title_slide(
        prs,
        title="Enhanced & Animated PowerPoint",
        subtitle="Combining Design and Animation Helpers",
        palette=palette,
    )

    # Add a slide transition for title slide
    anim.add_slide_transition(
        prs, slide_index, transition_type="fade", duration=1.0, trigger="on_click"
    )

    # Section Slide - Design Helpers
    slide_index = 1
    slide = design.create_section_slide(
        prs, title="Design Helpers", subtitle="Creating Visually Appealing Slides", palette=palette
    )

    # Add a different transition for this slide
    anim.add_slide_transition(
        prs, slide_index, transition_type="push", duration=1.5, trigger="on_click"
    )

    # Content Slide - Color Palettes
    slide_index = 2
    bullet_points = [
        "Professional color schemes with consistent palettes",
        "Enhanced typography with proper font hierarchy",
        "Layout helpers for grid-based designs",
        "Shape styling with text formatting built-in",
        "Background and content area management",
    ]

    slide = design.create_content_slide(
        prs, title="Design Features", bullet_points=bullet_points, palette=palette
    )

    # Get title shape for animation

    # Add animations to shapes
    # First animate the title
    anim.add_animation_metadata(
        prs,
        slide_index,
        0,  # shape 0 is usually the title
        animation_type="fade",
        trigger="on_click",
        duration=0.8,
        order=1,
    )

    # Then animate each bullet point paragraph
    bullet_shape_index = 1  # bullet points are usually in shape 1

    # Animate the bullets with sequential timing
    for i, _point in enumerate(bullet_points):
        anim.add_animation_metadata(
            prs,
            slide_index,
            bullet_shape_index,
            animation_type="fly_in",
            trigger="on_click",
            duration=0.5,
            delay=0.1 * i,  # staggered timing
            order=i + 2,  # start after title
            direction="from_left",
        )

    # Add a slide transition
    anim.add_slide_transition(
        prs, slide_index, transition_type="wipe", duration=1.0, trigger="on_click"
    )

    # Section Slide - Animation Helpers
    slide_index = 3
    slide = design.create_section_slide(
        prs,
        title="Animation Helpers",
        subtitle="Adding Movement to Your Presentations",
        palette=palette,
    )

    # Add a transition
    anim.add_slide_transition(
        prs, slide_index, transition_type="reveal", duration=1.0, trigger="on_click"
    )

    # Custom flowchart with animations
    slide_index = 4

    # Create a flowchart slide
    elements = [
        ("Start", 1, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "primary"),
        ("Process Data", 4, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.RECTANGLE, "secondary"),
        ("Evaluate", 7, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.DIAMOND, "accent1"),
        ("Output", 4, 4, 2, 0.75, MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "accent2"),
    ]

    connections = [(0, 1, "Input"), (1, 2, "Analyze"), (2, 3, "Results"), (3, 1, "Refine")]

    slide = design.create_flowchart_slide(
        prs,
        title="Animated Process Flow",
        elements=elements,
        connections=connections,
        palette=palette,
    )

    # Get shapes to animate
    # Title + elements + arrows = 1 title + 4 elements + 4 connections + labels
    shape_elements_start_index = 1  # After title

    # Add title animation
    anim.add_animation_metadata(
        prs,
        slide_index,
        0,  # Title is shape 0
        animation_type="fade",
        trigger="on_click",
        duration=0.5,
        order=1,
    )

    # Add animation to each element in sequence
    for i in range(len(elements)):
        shape_index = shape_elements_start_index + i
        anim.add_animation_metadata(
            prs,
            slide_index,
            shape_index,
            animation_type="zoom",
            trigger="after_previous",
            duration=0.4,
            delay=0.1,
            order=i + 2,  # Start after title
        )

    # Then animate connections (arrows and labels)
    connection_start_index = shape_elements_start_index + len(elements)
    for i in range(len(connections) * 2):  # Each connection has arrow and label
        shape_index = connection_start_index + i
        anim.add_animation_metadata(
            prs,
            slide_index,
            shape_index,
            animation_type="wipe",
            trigger="after_previous",
            duration=0.3,
            order=i + len(elements) + 2,  # Start after elements
        )

    # Add slide transition
    anim.add_slide_transition(
        prs, slide_index, transition_type="fade", duration=0.8, trigger="on_click"
    )

    # Create a custom Grid Layout slide with animations
    slide_index = 5
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank layout

    # Add title with styling
    design.create_title(slide, "Animated Grid Layout", font_size=36, color=palette.primary)

    # Add a colored background area
    design.add_solid_background(
        slide,
        color=design.hex_to_rgb("#f5f5f5"),  # Light gray
    )

    # Create a 2x2 grid layout
    grid = design.create_grid_layout(slide, 2, 2)

    # Add various styled shapes to each grid cell
    cell = grid[0][0]  # Top-left cell
    design.add_styled_rectangle(
        slide,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.primary,
        line_color=None,
        text="Rectangle",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    cell = grid[0][1]  # Top-right cell
    design.add_styled_oval(
        slide,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.secondary,
        line_color=None,
        text="Oval",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    cell = grid[1][0]  # Bottom-left cell
    design.add_styled_rounded_rectangle(
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
    design.add_styled_shape(
        slide,
        MSO_AUTO_SHAPE_TYPE.DIAMOND,
        left=cell[0] / Inches(1),
        top=cell[1] / Inches(1),
        width=cell[2] / Inches(1) - 0.1,
        height=cell[3] / Inches(1) - 0.1,
        fill_color=palette.accent2,
        line_color=None,
        text="Diamond",
        text_style={
            "font_size": 14,
            "bold": True,
            "color": design.RGBColor(255, 255, 255),
            "alignment": design.PP_ALIGN.CENTER,
        },
    )

    # Title is shape index 0
    # Animations for each shape with different effects
    anim.add_animation_metadata(
        prs,
        slide_index,
        0,  # Title
        animation_type="fade",
        trigger="on_click",
        duration=0.5,
        order=1,
    )

    # Rectangle - shape index 2 (after title and background)
    anim.add_animation_metadata(
        prs,
        slide_index,
        2,
        animation_type="fly_in",
        trigger="after_previous",
        duration=0.5,
        direction="from_left",
        order=2,
    )

    # Oval
    anim.add_animation_metadata(
        prs,
        slide_index,
        3,
        animation_type="fly_in",
        trigger="after_previous",
        duration=0.5,
        direction="from_right",
        order=3,
    )

    # Rounded Rectangle
    anim.add_animation_metadata(
        prs,
        slide_index,
        4,
        animation_type="fly_in",
        trigger="after_previous",
        duration=0.5,
        direction="from_bottom",
        order=4,
    )

    # Diamond
    anim.add_animation_metadata(
        prs, slide_index, 5, animation_type="zoom", trigger="after_previous", duration=0.5, order=5
    )

    # Add a transition
    anim.add_slide_transition(
        prs, slide_index, transition_type="fade", duration=0.8, trigger="on_click"
    )

    # Add animation guide slide
    slide_index = 6
    slide = anim.add_animation_guide_slide(prs)

    # Add a transition for the guide slide
    anim.add_slide_transition(
        prs, slide_index, transition_type="fade", duration=0.8, trigger="on_click"
    )

    # Create conclusion slide
    slide_index = 7
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
        "Combining Design and Animation for Better Presentations",
        left=2,
        top=4,
        width=6,
        height=0.5,
        font_size=18,
        font_name="Segoe UI",
        color=design.RGBColor(200, 200, 200),
        alignment=design.PP_ALIGN.CENTER,
    )

    # Animate title and subtitle
    anim.add_animation_metadata(
        prs,
        slide_index,
        1,  # Title
        animation_type="fade",
        trigger="on_click",
        duration=1.0,
        order=1,
    )

    anim.add_animation_metadata(
        prs,
        slide_index,
        2,  # Subtitle
        animation_type="fly_in",
        trigger="after_previous",
        duration=0.8,
        direction="from_bottom",
        order=2,
    )

    # Add a transition for conclusion
    anim.add_slide_transition(
        prs, slide_index, transition_type="fade", duration=1.5, trigger="on_click"
    )

    # Add animation instructions to slide notes
    anim.add_animation_instructions_to_notes(prs)

    # Generate animation and transition instruction files
    output_folder = Path("output_slides")
    output_folder.mkdir(exist_ok=True)

    anim.generate_animation_instructions(prs, output_folder / "animation_instructions.md")

    anim.generate_transition_instructions(prs, output_folder / "transition_instructions.md")

    # Export all metadata to a JSON file
    anim.export_presentation_metadata(prs, output_folder / "presentation_animation_metadata.json")

    # Save the presentation
    prs.save(str(output_folder / "enhanced_animated_presentation.pptx"))
    print(
        f"Enhanced and animated presentation saved to {output_folder}/enhanced_animated_presentation.pptx"
    )
    print(f"Animation instructions saved to {output_folder}/animation_instructions.md")
    print(f"Transition instructions saved to {output_folder}/transition_instructions.md")
    print(f"Animation metadata saved to {output_folder}/presentation_animation_metadata.json")

    return prs


if __name__ == "__main__":
    create_enhanced_animated_presentation()
