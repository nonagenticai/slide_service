#!/usr/bin/env python3
"""
PowerPoint Creator for Agentic AI Platform Use Cases

This script creates PowerPoint presentations showcasing various visualization
approaches for an agentic AI platform use case in a utility company scenario.
"""

import os
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# Constants
OUTPUT_DIR = Path("output_slides")
TITLE_FONT_SIZE = Pt(32)
SUBTITLE_FONT_SIZE = Pt(16)
BODY_FONT_SIZE = Pt(12)
MAIN_COLOR = RGBColor(0, 112, 192)  # Blue
ACCENT_COLOR = RGBColor(255, 102, 0)  # Orange
CHANNEL_LABELS = ["Phone", "SMS", "WhatsApp", "Mobile App", "Web Portal", "Email"]
API_LABELS = ["Meter Data API", "Usage Analysis API", "Billing API", "Customer Info API"]


def create_directory():
    """Create output directory if it doesn't exist."""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def add_title_slide(prs, title, subtitle=None):
    """Add a title slide to the presentation."""
    slide = prs.slides.add_slide(prs.slide_layouts[0])  # Title slide layout

    title_shape = slide.shapes.title
    title_shape.text = title
    title_shape.text_frame.paragraphs[0].font.size = TITLE_FONT_SIZE

    if subtitle:
        subtitle_shape = slide.placeholders[1]
        subtitle_shape.text = subtitle
        subtitle_shape.text_frame.paragraphs[0].font.size = SUBTITLE_FONT_SIZE

    return slide


def create_central_flow_diagram(prs):
    """Create a slide with a central flow diagram visualization, using extremely simple shapes."""
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank slide layout

    # Add title
    title = slide.shapes.title
    title.text = "Agentic AI Platform: Central Flow Diagram"

    # Use a background and simple text boxes only - no complex shapes or connectors
    # Create background
    background = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(0.5),
        Inches(1.5),  # x, y position
        Inches(9),
        Inches(4.5),  # width, height
    )
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(240, 240, 240)  # Light gray

    # Add channel boxes
    channel_titles = ["Phone", "SMS", "Web Portal", "Mobile App"]
    for i, channel in enumerate(channel_titles):
        # Create text box for each channel
        channel_box = slide.shapes.add_textbox(
            Inches(1),
            Inches(2 + i * 0.7),  # x, y position
            Inches(2),
            Inches(0.5),  # width, height
        )
        # Add a simple rectangle around it
        rect = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(1),
            Inches(2 + i * 0.7),  # x, y position
            Inches(2),
            Inches(0.5),  # width, height
        )
        rect.fill.solid()
        rect.fill.fore_color.rgb = RGBColor(255, 230, 153)  # Light yellow

        # Add text
        text_frame = channel_box.text_frame
        text_frame.text = channel
        text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        text_frame.paragraphs[0].font.bold = True

    # Add platform box in the center
    platform_box = slide.shapes.add_textbox(
        Inches(4),
        Inches(3),  # x, y position
        Inches(2),
        Inches(1),  # width, height
    )
    # Add a simple rectangle around it
    platform_rect = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(4),
        Inches(3),  # x, y position
        Inches(2),
        Inches(1),  # width, height
    )
    platform_rect.fill.solid()
    platform_rect.fill.fore_color.rgb = RGBColor(189, 215, 238)  # Light blue

    # Add text
    platform_text = platform_box.text_frame
    platform_text.text = "Agentic AI Platform"
    platform_text.paragraphs[0].alignment = PP_ALIGN.CENTER
    platform_text.paragraphs[0].font.bold = True

    # Add API boxes
    api_titles = ["Meter Data", "Usage Analysis", "Billing API"]
    for i, api in enumerate(api_titles):
        # Create text box for each API
        api_box = slide.shapes.add_textbox(
            Inches(7),
            Inches(2 + i * 0.7),  # x, y position
            Inches(2),
            Inches(0.5),  # width, height
        )
        # Add a simple rectangle around it
        rect = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(7),
            Inches(2 + i * 0.7),  # x, y position
            Inches(2),
            Inches(0.5),  # width, height
        )
        rect.fill.solid()
        rect.fill.fore_color.rgb = RGBColor(197, 224, 180)  # Light green

        # Add text
        text_frame = api_box.text_frame
        text_frame.text = api
        text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        text_frame.paragraphs[0].font.bold = True

    # Add a text description instead of connectors
    desc_box = slide.shapes.add_textbox(
        Inches(3),
        Inches(5),  # x, y position
        Inches(4),
        Inches(0.5),  # width, height
    )
    desc_box.text_frame.text = "Customer channels connect to AI Platform which integrates with APIs"
    desc_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    desc_box.text_frame.paragraphs[0].font.italic = True

    return slide


def create_sequence_diagram(prs):
    """Create a slide with a sequence diagram using swim lanes."""
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank slide layout

    # Add title
    title = slide.shapes.title
    title.text = "Agentic AI Platform: Sequence Diagram"

    # Create swim lanes
    lanes = ["Customer", "Agentic AI Platform", "Utility APIs"]
    lane_width = Inches(3)
    lane_height = Inches(5)

    lane_shapes = []
    for i, lane in enumerate(lanes):
        # Create lane rectangle
        shape = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(1 + i * 3),
            Inches(1.5),  # x, y position
            lane_width,
            lane_height,  # width, height
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(240, 240, 240)  # Light gray

        # Add lane title
        title_box = slide.shapes.add_textbox(
            Inches(1 + i * 3),
            Inches(1),  # x, y position
            lane_width,
            Inches(0.5),  # width, height
        )
        title_box.text_frame.text = lane
        title_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        title_box.text_frame.paragraphs[0].font.bold = True

        lane_shapes.append(shape)

    # Add sequence steps
    steps = [
        ("1. Customer submits query", 0, Inches(2)),
        ("2. Platform processes request", 1, Inches(2.5)),
        ("3. Platform calls APIs", 1, Inches(3)),
        ("4. APIs return data", 2, Inches(3.5)),
        ("5. Platform analyzes data", 1, Inches(4)),
        ("6. Results delivered to customer", 1, Inches(4.5)),
        ("7. Customer views analysis", 0, Inches(5)),
    ]

    for step_text, lane_idx, y_pos in steps:
        step_box = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
            Inches(1.25 + lane_idx * 3),
            y_pos,  # x, y position
            Inches(2.5),
            Inches(0.4),  # width, height
        )
        step_box.fill.solid()

        # Different colors for different lanes
        if lane_idx == 0:  # Customer
            step_box.fill.fore_color.rgb = RGBColor(255, 230, 153)  # Light yellow
        elif lane_idx == 1:  # Platform
            step_box.fill.fore_color.rgb = RGBColor(189, 215, 238)  # Light blue
        else:  # APIs
            step_box.fill.fore_color.rgb = RGBColor(197, 224, 180)  # Light green

        step_box.text_frame.text = step_text
        step_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER

    # Add turnaround time highlight as a rectangle (replaced cloud with rectangle)
    time_box = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,  # Changed from CLOUD to RECTANGLE
        Inches(7.5),
        Inches(3.5),  # x, y position
        Inches(2),
        Inches(1.2),  # width, height
    )
    time_box.fill.solid()
    time_box.fill.fore_color.rgb = RGBColor(255, 192, 0)  # Gold

    time_box.text_frame.text = "Fast response time:\n< 2 seconds"
    time_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    time_box.text_frame.paragraphs[0].font.bold = True

    return slide


def create_layered_architecture(prs):
    """Create a slide with a simplified layered architecture diagram."""
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank slide layout

    # Add title
    title = slide.shapes.title
    title.text = "Agentic AI Platform: Layered Architecture"

    # Create a background rectangle
    background = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(0.5),
        Inches(1.5),  # x, y position
        Inches(9),
        Inches(4.5),  # width, height
    )
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(240, 240, 240)  # Light gray

    # Create three layers with simple rectangles
    layers = [
        ("Customer Channels", RGBColor(255, 230, 153), Inches(1.5)),  # Light yellow
        ("Agentic AI Platform", RGBColor(189, 215, 238), Inches(3)),  # Light blue
        ("Utility Backend APIs", RGBColor(197, 224, 180), Inches(4.5)),  # Light green
    ]

    for _i, (layer_name, color, y_pos) in enumerate(layers):
        # Add a text box for each layer
        layer_box = slide.shapes.add_textbox(
            Inches(1.5),
            y_pos,  # x, y position
            Inches(7),
            Inches(1),  # width, height
        )

        # Add a rectangle behind it
        rect = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(1.5),
            y_pos,  # x, y position
            Inches(7),
            Inches(1),  # width, height
        )
        rect.fill.solid()
        rect.fill.fore_color.rgb = color

        # Add layer title to the text box
        layer_box.text_frame.text = layer_name
        layer_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        layer_box.text_frame.paragraphs[0].font.bold = True

    # Add text boxes for channel and API details
    channel_text = slide.shapes.add_textbox(
        Inches(1),
        Inches(1),  # x, y position
        Inches(8),
        Inches(0.4),  # width, height
    )
    channel_text.text_frame.text = "Channels: Phone, SMS, Web Portal, Mobile App"
    channel_text.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    channel_text.text_frame.paragraphs[0].font.bold = True

    api_text = slide.shapes.add_textbox(
        Inches(1),
        Inches(5.6),  # x, y position
        Inches(8),
        Inches(0.4),  # width, height
    )
    api_text.text_frame.text = "APIs: Meter Data, Usage Analysis, Billing, Customer Info"
    api_text.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    api_text.text_frame.paragraphs[0].font.bold = True

    # Add a text description instead of connectors
    desc_box = slide.shapes.add_textbox(
        Inches(3),
        Inches(2.6),  # x, y position
        Inches(4),
        Inches(0.4),  # width, height
    )
    desc_box.text_frame.text = "↓ Data flows through layers ↓"
    desc_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    desc_box.text_frame.paragraphs[0].font.italic = True

    desc_box2 = slide.shapes.add_textbox(
        Inches(3),
        Inches(4.1),  # x, y position
        Inches(4),
        Inches(0.4),  # width, height
    )
    desc_box2.text_frame.text = "↓ API integration ↓"
    desc_box2.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    desc_box2.text_frame.paragraphs[0].font.italic = True

    return slide


def create_journey_map(prs):
    """Create a slide with a simplified customer journey map visualization."""
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank slide layout

    # Add title
    title = slide.shapes.title
    title.text = "Agentic AI Platform: Customer Journey Map"

    # Create a background rectangle
    background = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(0.5),
        Inches(1.5),  # x, y position
        Inches(9),
        Inches(4.5),  # width, height
    )
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(240, 240, 240)  # Light gray

    # Create three journey steps as rectangles
    steps = [
        ("Step 1: Customer Inquiry", Inches(1), RGBColor(255, 230, 153)),  # Light yellow
        ("Step 2: AI Processing", Inches(4), RGBColor(189, 215, 238)),  # Light blue
        ("Step 3: Results Delivery", Inches(7), RGBColor(197, 224, 180)),  # Light green
    ]

    for _i, (step_name, x_pos, color) in enumerate(steps):
        # Add text box for each step
        step_box = slide.shapes.add_textbox(
            x_pos,
            Inches(2),  # x, y position
            Inches(2),
            Inches(3),  # width, height
        )

        # Add rectangle behind it
        rect = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            x_pos,
            Inches(2),  # x, y position
            Inches(2),
            Inches(3),  # width, height
        )
        rect.fill.solid()
        rect.fill.fore_color.rgb = color

        # Add step title
        step_box.text_frame.text = step_name
        step_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        step_box.text_frame.paragraphs[0].font.bold = True

    # Add detail boxes for each step
    details = [
        ("Customer sends query through any channel", 0, Inches(2.7)),
        ("Platform processes request and calls APIs", 1, Inches(2.7)),
        ("Analysis results delivered to customer", 2, Inches(2.7)),
    ]

    for detail_text, step_idx, y_pos in details:
        # Add text box
        detail_box_text = slide.shapes.add_textbox(
            Inches(1 + step_idx * 3) + Inches(0.1), y_pos, Inches(1.8), Inches(0.6)
        )
        # Add rectangle
        detail_box = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(1 + step_idx * 3) + Inches(0.1),
            y_pos,
            Inches(1.8),
            Inches(0.6),
        )
        detail_box.fill.solid()
        detail_box.fill.fore_color.rgb = RGBColor(255, 255, 255)  # White

        # Add text
        detail_box_text.text_frame.text = detail_text
        detail_box_text.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        detail_box_text.text_frame.paragraphs[0].font.size = Pt(9)

    # Add text arrows instead of connectors
    arrow1 = slide.shapes.add_textbox(
        Inches(3.1),
        Inches(3.3),  # x, y position
        Inches(0.8),
        Inches(0.5),  # width, height
    )
    arrow1.text_frame.text = "→→→"
    arrow1.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    arrow1.text_frame.paragraphs[0].font.size = Pt(14)
    arrow1.text_frame.paragraphs[0].font.bold = True

    arrow2 = slide.shapes.add_textbox(
        Inches(6.1),
        Inches(3.3),  # x, y position
        Inches(0.8),
        Inches(0.5),  # width, height
    )
    arrow2.text_frame.text = "→→→"
    arrow2.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    arrow2.text_frame.paragraphs[0].font.size = Pt(14)
    arrow2.text_frame.paragraphs[0].font.bold = True

    # Add callout as a rectangle
    callout = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(3.5),
        Inches(4.5),  # x, y position
        Inches(3),
        Inches(1),  # width, height
    )
    callout.fill.solid()
    callout.fill.fore_color.rgb = RGBColor(255, 192, 0)  # Gold

    # Add text to callout
    callout_text = slide.shapes.add_textbox(
        Inches(3.5),
        Inches(4.5),  # x, y position
        Inches(3),
        Inches(1),  # width, height
    )
    callout_text.text_frame.text = "Seamless omnichannel experience"
    callout_text.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    callout_text.text_frame.paragraphs[0].font.bold = True

    return slide


def create_dashboard_mockup(prs):
    """Create a slide with an interactive dashboard mockup."""
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank slide layout

    # Add title
    title = slide.shapes.title
    title.text = "Agentic AI Platform: Interactive Dashboard Mockup"

    # Create a background rectangle (like the swim lanes in sequence diagram)
    background = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(0.5),
        Inches(1.5),  # x, y position
        Inches(9),
        Inches(4.5),  # width, height
    )
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(240, 240, 240)  # Light gray

    # Add dashboard frame as a rounded rectangle (like step boxes)
    dashboard = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
        Inches(2),
        Inches(2),  # x, y position
        Inches(6),
        Inches(3.5),  # width, height
    )
    dashboard.fill.solid()
    dashboard.fill.fore_color.rgb = RGBColor(255, 255, 255)  # White

    # Add dashboard header using a rectangle (like in sequence diagram)
    header_box = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(2),
        Inches(2),  # x, y position
        Inches(6),
        Inches(0.5),  # width, height
    )
    header_box.fill.solid()
    header_box.fill.fore_color.rgb = RGBColor(189, 215, 238)  # Light blue

    # Add header text as a separate text box (like in sequence diagram)
    header_text = slide.shapes.add_textbox(
        Inches(2),
        Inches(2),  # x, y position
        Inches(6),
        Inches(0.5),  # width, height
    )
    header_text.text_frame.text = "Electricity Usage Analysis Dashboard"
    header_text.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    header_text.text_frame.paragraphs[0].font.bold = True

    # Add chart title as a separate text box (like in sequence diagram)
    chart_title = slide.shapes.add_textbox(
        Inches(2),
        Inches(2.7),  # x, y position
        Inches(6),
        Inches(0.3),  # width, height
    )
    chart_title.text_frame.text = "Monthly Electricity Consumption (kWh)"
    chart_title.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    chart_title.text_frame.paragraphs[0].font.size = Pt(10)

    # Add chart bars as separate shapes (like step boxes in sequence diagram)
    months = ["Jan", "Apr", "Jul", "Oct"]
    heights = [1.0, 1.5, 2.0, 1.2]  # Heights in inches

    for i, (month, height) in enumerate(zip(months, heights, strict=True)):
        # Create bar
        bar = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(2.5 + i * 1.25),
            Inches(5.0 - height),  # x, y position
            Inches(0.75),
            Inches(height),  # width, height
        )
        bar.fill.solid()

        # Alternate colors like in sequence diagram
        if i % 2 == 0:
            bar.fill.fore_color.rgb = RGBColor(189, 215, 238)  # Light blue
        else:
            bar.fill.fore_color.rgb = RGBColor(255, 230, 153)  # Light yellow

        # Add month label as separate text box (like in sequence diagram)
        month_label = slide.shapes.add_textbox(
            Inches(2.5 + i * 1.25),
            Inches(5.1),  # x, y position
            Inches(0.75),
            Inches(0.2),  # width, height
        )
        month_label.text_frame.text = month
        month_label.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        month_label.text_frame.paragraphs[0].font.size = Pt(9)

    # Add insights callout as a rectangle (replaced cloud with rectangle)
    insights = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,  # Changed from CLOUD to RECTANGLE
        Inches(7),
        Inches(3),  # x, y position
        Inches(2),
        Inches(1.2),  # width, height
    )
    insights.fill.solid()
    insights.fill.fore_color.rgb = RGBColor(255, 192, 0)  # Gold

    insights.text_frame.text = "Key Insights:\nSave $45/month using\noff-peak hours"
    insights.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER

    return slide


def create_pptx(output_name="agentic_ai_utility_use_case.pptx"):
    """Create a PPTX file with five different visualization slides in a single presentation."""
    # Create a fresh presentation with minimal complexity
    prs = Presentation()

    # Create the title slide first
    add_title_slide(
        prs,
        "Agentic AI Platform: Utility Company Use Case",
        "Omnichannel Customer Experience for Energy Analysis",
    )

    # Create output directory
    create_directory()

    # Add a table of contents slide
    toc_slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank slide layout
    toc_title = toc_slide.shapes.title
    toc_title.text = "Visualizations Included"

    toc_box = toc_slide.shapes.add_textbox(
        Inches(2),
        Inches(2),  # x, y position
        Inches(6),
        Inches(3),  # width, height
    )
    toc_box.text_frame.text = (
        "This presentation includes the following visualizations:\n\n"
        "1. Central Flow Diagram\n"
        "2. Sequence Diagram\n"
        "3. Layered Architecture\n"
        "4. Customer Journey Map\n"
        "5. Interactive Dashboard Mockup"
    )

    # Add each visualization slide safely
    # Central Flow Diagram
    print("Adding Central Flow Diagram to main presentation...")
    create_central_flow_diagram(prs)

    # Sequence Diagram
    print("Adding Sequence Diagram to main presentation...")
    create_sequence_diagram(prs)

    # Layered Architecture
    print("Adding Layered Architecture to main presentation...")
    create_layered_architecture(prs)

    # Customer Journey Map
    print("Adding Customer Journey Map to main presentation...")
    create_journey_map(prs)

    # Dashboard Mockup
    print("Adding Dashboard Mockup to main presentation...")
    create_dashboard_mockup(prs)

    # Conclusion slide
    conclusion_slide = prs.slides.add_slide(prs.slide_layouts[5])
    conclusion_title = conclusion_slide.shapes.title
    conclusion_title.text = "Thank You"

    conclusion_box = conclusion_slide.shapes.add_textbox(
        Inches(2), Inches(2.5), Inches(6), Inches(1.5)
    )
    conclusion_box.text_frame.text = (
        "The Agentic AI Platform enables seamless customer interactions\n"
        "across multiple channels for utility companies.\n\n"
        "For more information, please contact us."
    )
    conclusion_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER

    # Save the final presentation
    final_output_path = OUTPUT_DIR / output_name
    prs.save(str(final_output_path))

    return str(final_output_path)


def main():
    """Main entry point for the script."""
    # Create the directory if it doesn't exist
    create_directory()

    # Create the main PPTX with all visualizations as a single file
    output_path = create_pptx()
    print(f"Created complete PowerPoint presentation: {output_path}")

    # Also create individual slides as separate PPTX files as a backup
    print("Creating individual slide files as backup...")

    # Central Flow Diagram
    try:
        prs = Presentation()
        add_title_slide(prs, "Central Flow Diagram", "Agentic AI Platform for Utility Companies")
        create_central_flow_diagram(prs)
        prs.save(str(OUTPUT_DIR / "central_flow_diagram.pptx"))
        print("Created central_flow_diagram.pptx")
    except Exception as e:
        print(f"Error creating central_flow_diagram.pptx: {e}")

    # Sequence Diagram
    try:
        prs = Presentation()
        add_title_slide(prs, "Sequence Diagram", "Agentic AI Platform for Utility Companies")
        create_sequence_diagram(prs)
        prs.save(str(OUTPUT_DIR / "sequence_diagram.pptx"))
        print("Created sequence_diagram.pptx")
    except Exception as e:
        print(f"Error creating sequence_diagram.pptx: {e}")

    # Layered Architecture
    try:
        prs = Presentation()
        add_title_slide(prs, "Layered Architecture", "Agentic AI Platform for Utility Companies")
        create_layered_architecture(prs)
        prs.save(str(OUTPUT_DIR / "layered_architecture.pptx"))
        print("Created layered_architecture.pptx")
    except Exception as e:
        print(f"Error creating layered_architecture.pptx: {e}")

    # Customer Journey Map
    try:
        prs = Presentation()
        add_title_slide(prs, "Customer Journey Map", "Agentic AI Platform for Utility Companies")
        create_journey_map(prs)
        prs.save(str(OUTPUT_DIR / "customer_journey.pptx"))
        print("Created customer_journey.pptx")
    except Exception as e:
        print(f"Error creating customer_journey.pptx: {e}")

    # Dashboard Mockup
    try:
        prs = Presentation()
        add_title_slide(
            prs, "Interactive Dashboard Mockup", "Agentic AI Platform for Utility Companies"
        )
        create_dashboard_mockup(prs)
        prs.save(str(OUTPUT_DIR / "dashboard_mockup.pptx"))
        print("Created dashboard_mockup.pptx")
    except Exception as e:
        print(f"Error creating dashboard_mockup.pptx: {e}")

    print(f"Created individual PowerPoint slides in {OUTPUT_DIR}/")

    # Remove temporary files if they exist
    temp_files = [
        "temp_title_only.pptx",
        "temp_with_central_flow.pptx",
        "temp_with_sequence.pptx",
        "temp_with_layered.pptx",
        "temp_with_journey.pptx",
        "temp_with_dashboard.pptx",
    ]
    for temp_file in temp_files:
        temp_path = OUTPUT_DIR / temp_file
        if os.path.exists(temp_path):
            os.remove(temp_path)
            print(f"Removed temporary file: {temp_file}")


if __name__ == "__main__":
    main()
