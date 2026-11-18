#!/usr/bin/env python3
"""
Test suite for PowerPoint Design Helpers

This module contains unit tests for the pptx_design_helpers module.
"""

import sys
import unittest
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples"))

import pptx_design_helpers as design  # ty: ignore[import-not-found]


class TestColorHelpers(unittest.TestCase):
    """Test case for color helper functions."""

    def test_rgb_color(self):
        """Test creating RGB color objects."""
        color = design.rgb_color(255, 0, 0)
        self.assertEqual(color.rgb, (255, 0, 0))

    def test_hex_to_rgb(self):
        """Test converting hex color to RGB color object."""
        color = design.hex_to_rgb("#FF0000")
        self.assertEqual(color.rgb, (255, 0, 0))

        # Test without hash
        color = design.hex_to_rgb("00FF00")
        self.assertEqual(color.rgb, (0, 255, 0))

    def test_color_palette(self):
        """Test creating color palette."""
        palette = design.ColorPalette()
        self.assertEqual(palette.primary.rgb, (0, 112, 192))
        self.assertEqual(palette.secondary.rgb, (0, 176, 80))

        # Test from_hex class method
        palette = design.ColorPalette.from_hex(primary="#FF0000", secondary="#00FF00")
        self.assertEqual(palette.primary.rgb, (255, 0, 0))
        self.assertEqual(palette.secondary.rgb, (0, 255, 0))

    def test_color_scheme(self):
        """Test getting color schemes."""
        palette = design.ColorPalette()
        scheme = palette.get_color_scheme()
        self.assertEqual(scheme["title"], palette.primary)
        self.assertEqual(scheme["body"], palette.dark)

        # Test different scheme
        scheme = palette.get_color_scheme("dark")
        self.assertEqual(scheme["background"], palette.dark)
        self.assertEqual(scheme["title"], palette.light)

        # Test invalid scheme (should return default)
        scheme = palette.get_color_scheme("nonexistent")
        self.assertEqual(scheme["title"], palette.primary)


class TestDesignHelpers(unittest.TestCase):
    """Test case for the main design helper functions."""

    def setUp(self):
        """Set up for each test."""
        self.prs = Presentation()
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[5])  # Blank layout
        self.palette = design.ColorPalette()

        # Create output directory if it doesn't exist
        self.output_dir = Path("test_output")
        self.output_dir.mkdir(exist_ok=True)

    def test_apply_text_style(self):
        """Test applying text style to a text frame."""
        textbox = self.slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))

        # Test with text parameter
        design.apply_text_style(
            textbox.text_frame,
            text="Test Text",
            font_size=24,
            font_name="Arial",
            bold=True,
            color=RGBColor(255, 0, 0),
            alignment=PP_ALIGN.CENTER,
        )

        self.assertEqual(textbox.text_frame.text, "Test Text")

        # Check paragraph properties
        para = textbox.text_frame.paragraphs[0]
        self.assertEqual(para.alignment, PP_ALIGN.CENTER)

        # Check run properties
        run = para.runs[0]
        self.assertEqual(run.font.size.pt, 24)
        self.assertEqual(run.font.name, "Arial")
        self.assertTrue(run.font.bold)
        self.assertEqual(run.font.color.rgb, (255, 0, 0))

    def test_add_text_box(self):
        """Test adding a styled text box."""
        textbox = design.add_text_box(
            self.slide,
            "Test Text Box",
            left=1,
            top=1,
            width=3,
            height=1,
            font_size=18,
            bold=True,
            color=RGBColor(0, 0, 255),
            alignment=PP_ALIGN.RIGHT,
        )

        self.assertEqual(textbox.text_frame.text, "Test Text Box")

        # Check paragraph properties
        para = textbox.text_frame.paragraphs[0]
        self.assertEqual(para.alignment, PP_ALIGN.RIGHT)

        # Check run properties
        run = para.runs[0]
        self.assertEqual(run.font.size.pt, 18)
        self.assertTrue(run.font.bold)
        self.assertEqual(run.font.color.rgb, (0, 0, 255))

    def test_add_styled_shape(self):
        """Test adding a styled shape."""
        shape = design.add_styled_shape(
            self.slide,
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            left=2,
            top=2,
            width=4,
            height=2,
            fill_color=RGBColor(0, 255, 0),
            line_color=RGBColor(0, 0, 0),
            line_width=Pt(2),
            text="Test Shape",
            text_style={
                "font_size": 14,
                "bold": True,
                "color": RGBColor(255, 255, 255),
                "alignment": PP_ALIGN.CENTER,
            },
        )

        self.assertEqual(shape.text_frame.text, "Test Shape")

        # Check fill and line properties
        self.assertEqual(shape.fill.fore_color.rgb, (0, 255, 0))
        self.assertEqual(shape.line.color.rgb, (0, 0, 0))

        # Check text properties
        para = shape.text_frame.paragraphs[0]
        self.assertEqual(para.alignment, PP_ALIGN.CENTER)
        self.assertEqual(para.font.size.pt, 14)
        self.assertTrue(para.font.bold)
        self.assertEqual(para.font.color.rgb, (255, 255, 255))

    def test_add_styled_rectangle(self):
        """Test adding a styled rectangle."""
        rect = design.add_styled_rectangle(
            self.slide,
            left=1,
            top=1,
            width=2,
            height=1,
            fill_color=RGBColor(255, 0, 0),
            text="Rectangle",
        )

        self.assertEqual(rect.text_frame.text, "Rectangle")
        self.assertEqual(rect.fill.fore_color.rgb, (255, 0, 0))

    def test_add_styled_rounded_rectangle(self):
        """Test adding a styled rounded rectangle."""
        rect = design.add_styled_rounded_rectangle(
            self.slide,
            left=1,
            top=2,
            width=2,
            height=1,
            fill_color=RGBColor(0, 0, 255),
            text="Rounded Rectangle",
        )

        self.assertEqual(rect.text_frame.text, "Rounded Rectangle")
        self.assertEqual(rect.fill.fore_color.rgb, (0, 0, 255))

    def test_add_styled_oval(self):
        """Test adding a styled oval."""
        oval = design.add_styled_oval(
            self.slide,
            left=4,
            top=1,
            width=2,
            height=1,
            fill_color=RGBColor(255, 255, 0),
            text="Oval",
        )

        self.assertEqual(oval.text_frame.text, "Oval")
        self.assertEqual(oval.fill.fore_color.rgb, (255, 255, 0))

    def test_add_solid_background(self):
        """Test adding a solid background."""
        bg = design.add_solid_background(self.slide, color=RGBColor(200, 200, 200))

        self.assertEqual(bg.fill.fore_color.rgb, (200, 200, 200))

        # Make sure it was sent to back
        # Background should be the first shape in spTree (after title and body placeholders)
        self.assertEqual(bg._element, self.slide.shapes._spTree[2])

    def test_add_content_area(self):
        """Test adding a content area."""
        content = design.add_content_area(
            self.slide, margin=1, fill_color=RGBColor(240, 240, 240), line_color=RGBColor(0, 0, 0)
        )

        self.assertEqual(content.fill.fore_color.rgb, (240, 240, 240))
        self.assertEqual(content.line.color.rgb, (0, 0, 0))

    def test_create_grid_layout(self):
        """Test creating a grid layout."""
        grid = design.create_grid_layout(self.slide, 2, 3)

        # Should have 2 rows
        self.assertEqual(len(grid), 2)

        # Each row should have 3 columns
        self.assertEqual(len(grid[0]), 3)
        self.assertEqual(len(grid[1]), 3)

        # Each cell should be a 4-tuple (left, top, width, height)
        for row in grid:
            for cell in row:
                self.assertEqual(len(cell), 4)

    def test_create_column_layout(self):
        """Test creating a column layout."""
        columns = design.create_column_layout(self.slide, 3)

        # Should have 3 columns
        self.assertEqual(len(columns), 3)

        # Each column should be a 4-tuple (left, top, width, height)
        for col in columns:
            self.assertEqual(len(col), 4)

    def test_create_row_layout(self):
        """Test creating a row layout."""
        rows = design.create_row_layout(self.slide, 4)

        # Should have 4 rows
        self.assertEqual(len(rows), 4)

        # Each row should be a 4-tuple (left, top, width, height)
        for row in rows:
            self.assertEqual(len(row), 4)


class TestSlideCreators(unittest.TestCase):
    """Test case for slide creator functions."""

    def setUp(self):
        """Set up for each test."""
        self.prs = Presentation()
        self.palette = design.ColorPalette()

        # Create output directory if it doesn't exist
        self.output_dir = Path("test_output")
        self.output_dir.mkdir(exist_ok=True)

    def test_create_title_slide(self):
        """Test creating a title slide."""
        slide = design.create_title_slide(
            self.prs, title="Test Title Slide", subtitle="Test Subtitle", palette=self.palette
        )

        self.assertEqual(slide.shapes.title.text, "Test Title Slide")
        self.assertEqual(slide.placeholders[1].text, "Test Subtitle")

    def test_create_section_slide(self):
        """Test creating a section slide."""
        slide = design.create_section_slide(
            self.prs, title="Test Section", subtitle="Section Description", palette=self.palette
        )

        # Find title and subtitle by looking at text boxes
        has_title = False
        has_subtitle = False

        for shape in slide.shapes:
            if hasattr(shape, "text_frame"):
                if shape.text_frame.text == "Test Section":
                    has_title = True
                elif shape.text_frame.text == "Section Description":
                    has_subtitle = True

        self.assertTrue(has_title)
        self.assertTrue(has_subtitle)

    def test_create_content_slide(self):
        """Test creating a content slide."""
        bullet_points = ["Point 1", "Point 2", "Point 3"]

        slide = design.create_content_slide(
            self.prs, title="Test Content Slide", bullet_points=bullet_points, palette=self.palette
        )

        self.assertEqual(slide.shapes.title.text, "Test Content Slide")

        # Check if bullet points are in any text frame
        bullet_text = ""
        for shape in slide.shapes:
            if hasattr(shape, "text_frame"):
                bullet_text += shape.text_frame.text

        for point in bullet_points:
            self.assertIn(point, bullet_text)

    def test_create_comparison_slide(self):
        """Test creating a comparison slide."""
        left_points = ["Left 1", "Left 2"]
        right_points = ["Right 1", "Right 2"]

        slide = design.create_comparison_slide(
            self.prs,
            title="Test Comparison",
            left_title="Left Side",
            right_title="Right Side",
            left_points=left_points,
            right_points=right_points,
            palette=self.palette,
        )

        self.assertEqual(slide.shapes.title.text, "Test Comparison")

        # Check if points are in any text frame
        slide_text = ""
        for shape in slide.shapes:
            if hasattr(shape, "text_frame"):
                slide_text += shape.text_frame.text

        # Check titles and points
        self.assertIn("Left Side", slide_text)
        self.assertIn("Right Side", slide_text)

        for point in left_points + right_points:
            self.assertIn(point, slide_text)

    def test_create_flowchart_slide(self):
        """Test creating a flowchart slide."""
        elements = [
            ("Start", 1, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "primary"),
            ("End", 5, 2, 2, 0.75, MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "secondary"),
        ]

        connections = [(0, 1, "Flow")]

        slide = design.create_flowchart_slide(
            self.prs,
            title="Test Flowchart",
            elements=elements,
            connections=connections,
            palette=self.palette,
        )

        self.assertEqual(slide.shapes.title.text, "Test Flowchart")

        # Check if elements are in any text frame
        slide_text = ""
        for shape in slide.shapes:
            if hasattr(shape, "text_frame"):
                slide_text += shape.text_frame.text

        # Check element and connection labels
        self.assertIn("Start", slide_text)
        self.assertIn("End", slide_text)
        self.assertIn("Flow", slide_text)

    def test_apply_theme(self):
        """Test applying a theme to a presentation."""
        theme = design.apply_theme(self.prs, "modern")

        # Check that theme was applied
        self.assertTrue(hasattr(self.prs, "_theme"))

        # Check theme properties
        self.assertEqual(theme["title_font"], "Segoe UI")
        self.assertEqual(theme["title_size"], 36)

        # Check palette in theme
        self.assertEqual(theme["palette"].primary.rgb, (52, 152, 219))  # #3498db

    def test_get_current_theme(self):
        """Test getting the current theme."""
        # Apply a theme first
        design.apply_theme(self.prs, "corporate")

        # Get current theme
        theme = design.get_current_theme(self.prs)

        # Check theme properties
        self.assertEqual(theme["title_font"], "Arial")
        self.assertEqual(theme["palette"].primary.rgb, (0, 51, 102))  # #003366

    def test_save_presentation(self):
        """Test saving a presentation with applied styles."""
        # Create a simple presentation
        design.apply_theme(self.prs, "modern")
        palette = design.get_current_theme(self.prs)["palette"]

        # Add a title slide
        design.create_title_slide(
            self.prs,
            title="Test Presentation",
            subtitle="Created with Design Helpers",
            palette=palette,
        )

        # Add a content slide
        design.create_content_slide(
            self.prs,
            title="Test Content",
            bullet_points=["Point 1", "Point 2", "Point 3"],
            palette=palette,
        )

        # Save the presentation
        output_path = self.output_dir / "test_design_helpers.pptx"
        self.prs.save(str(output_path))

        # Check that file was created
        self.assertTrue(output_path.exists())

        # Clean up
        if output_path.exists():
            output_path.unlink()


class TestAnimationNotes(unittest.TestCase):
    """Test case for animation notes helpers."""

    def setUp(self):
        """Set up for each test."""
        self.prs = Presentation()
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[5])
        self.shape = self.slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(1)
        )

    def test_get_animation_notes(self):
        """Test generating animation notes."""
        notes = design.get_animation_notes(animation_type="fade", order=2, duration=0.5, delay=0.2)

        self.assertIn("FADE", notes)
        self.assertIn("Order: 2", notes)
        self.assertIn("Duration: 0.5s", notes)
        self.assertIn("Delay: 0.2s", notes)

    def test_add_animation_notes_to_shape(self):
        """Test adding animation notes to a shape."""
        shape = design.add_animation_notes_to_shape(
            self.shape, animation_type="fly_in", order=3, duration=0.8, delay=0.1
        )

        # Get description (alt text) from shape
        descr = shape._element.xpath(".//p:cNvPr")[0].get("descr")

        self.assertIn("FLY_IN", descr)
        self.assertIn("Order: 3", descr)
        self.assertIn("Duration: 0.8s", descr)
        self.assertIn("Delay: 0.1s", descr)


def run_tests():
    """Run all tests."""
    unittest.main()


if __name__ == "__main__":
    run_tests()
