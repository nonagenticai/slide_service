#!/usr/bin/env python3
"""
Test module for pptx_creator.py

This script tests the functionality of the PowerPoint creator script
to ensure that it creates slides as expected.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples"))

from pptx import Presentation
from pptx_creator import (  # ty: ignore[import-not-found]
    OUTPUT_DIR,
    add_title_slide,
    create_central_flow_diagram,
    create_dashboard_mockup,
    create_directory,
    create_journey_map,
    create_layered_architecture,
    create_pptx,
    create_sequence_diagram,
    main,
)


class TestPPTXCreator(unittest.TestCase):
    """Test case for the PPTX Creator functionality."""

    def setUp(self):
        """Set up the test environment."""
        # Ensure the output directory exists
        create_directory()
        self.test_pptx = OUTPUT_DIR / "test_presentation.pptx"

    def tearDown(self):
        """Clean up after tests."""
        # Remove test presentation if it exists
        if os.path.exists(self.test_pptx):
            os.remove(self.test_pptx)

    def test_create_directory(self):
        """Test that the output directory is created."""
        # Ensure directory exists after calling function
        create_directory()
        self.assertTrue(os.path.exists(OUTPUT_DIR))

    def test_add_title_slide(self):
        """Test adding a title slide to a presentation."""
        prs = Presentation()
        slide = add_title_slide(prs, "Test Title", "Test Subtitle")

        # Check that a slide was added
        self.assertEqual(len(prs.slides), 1)

        # Check that the title text is set correctly
        self.assertEqual(slide.shapes.title.text, "Test Title")

        # Check that the subtitle text is set correctly
        self.assertEqual(slide.placeholders[1].text, "Test Subtitle")

    def test_create_central_flow_diagram(self):
        """Test creating a central flow diagram slide."""
        prs = Presentation()
        slide = create_central_flow_diagram(prs)

        # Check that a slide was added
        self.assertEqual(len(prs.slides), 1)

        # Check slide title
        self.assertEqual(slide.shapes.title.text, "Agentic AI Platform: Central Flow Diagram")

        # Check that shapes were added (title + hub + 6 channels + arrows + textbox)
        # The exact count may vary but should be at least 10
        self.assertGreaterEqual(len(slide.shapes), 10)

    def test_create_sequence_diagram(self):
        """Test creating a sequence diagram slide."""
        prs = Presentation()
        slide = create_sequence_diagram(prs)

        # Check that a slide was added
        self.assertEqual(len(prs.slides), 1)

        # Check slide title
        self.assertEqual(slide.shapes.title.text, "Agentic AI Platform: Sequence Diagram")

        # Check that shapes were added (title + lanes + steps + time box)
        # The exact count may vary but should be at least 12
        self.assertGreaterEqual(len(slide.shapes), 12)

    def test_create_layered_architecture(self):
        """Test creating a layered architecture slide."""
        prs = Presentation()
        slide = create_layered_architecture(prs)

        # Check that a slide was added
        self.assertEqual(len(prs.slides), 1)

        # Check slide title
        self.assertEqual(slide.shapes.title.text, "Agentic AI Platform: Layered Architecture")

        # Check that shapes were added (title + 3 layers + channel icons + API icons + arrows)
        # The exact count may vary but should be at least 12
        self.assertGreaterEqual(len(slide.shapes), 12)

    def test_create_journey_map(self):
        """Test creating a journey map slide."""
        prs = Presentation()
        slide = create_journey_map(prs)

        # Check that a slide was added
        self.assertEqual(len(prs.slides), 1)

        # Check slide title
        self.assertEqual(slide.shapes.title.text, "Agentic AI Platform: Customer Journey Map")

        # Check that shapes were added (title + panels + content boxes + icons + arrows)
        # The exact count may vary but should be at least 15
        self.assertGreaterEqual(len(slide.shapes), 15)

    def test_create_dashboard_mockup(self):
        """Test creating a dashboard mockup slide."""
        prs = Presentation()
        slide = create_dashboard_mockup(prs)

        # Check that a slide was added
        self.assertEqual(len(prs.slides), 1)

        # Check slide title
        self.assertEqual(
            slide.shapes.title.text, "Agentic AI Platform: Interactive Dashboard Mockup"
        )

        # Check that shapes were added (title + dashboard container + header + chart + bars + insights + callouts)
        # The exact count may vary but should be at least 15
        self.assertGreaterEqual(len(slide.shapes), 15)

    def test_create_pptx(self):
        """Test creating a complete PowerPoint presentation."""
        output_path = create_pptx("test_presentation.pptx")

        # Check that file was created
        self.assertTrue(os.path.exists(output_path))

        # Check file content
        prs = Presentation(output_path)

        # Should have 8 slides (title + toc + 5 visualization + conclusion)
        self.assertEqual(len(prs.slides), 8)

        # Check title of first slide
        self.assertEqual(
            prs.slides[0].shapes.title.text, "Agentic AI Platform: Utility Company Use Case"
        )

    def test_main_function(self):
        """Test the main function."""
        # Call the main function
        main()

        # Check that the main presentation was created
        main_pptx = OUTPUT_DIR / "agentic_ai_utility_use_case.pptx"
        self.assertTrue(os.path.exists(main_pptx))

        # Check that individual slides were created
        individual_slides = [
            "central_flow_diagram.pptx",
            "sequence_diagram.pptx",
            "layered_architecture.pptx",
            "customer_journey.pptx",
            "dashboard_mockup.pptx",
        ]

        for slide in individual_slides:
            self.assertTrue(os.path.exists(OUTPUT_DIR / slide))

            # Clean up the files for next tests
            os.remove(OUTPUT_DIR / slide)

        # Clean up the main presentation
        os.remove(main_pptx)


if __name__ == "__main__":
    unittest.main()
