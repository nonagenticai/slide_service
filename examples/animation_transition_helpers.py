#!/usr/bin/env python3
"""
PowerPoint Animation and Transition Helpers

This module provides helper functions for adding animation and transition
guidance to PowerPoint presentations. Since python-pptx doesn't directly
support animations, these functions add metadata and instructions that can
be followed when manually finalizing the presentation in PowerPoint.
"""

import json

from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# ====================== ANIMATION METADATA ====================== #


def add_animation_metadata(
    presentation,
    slide_index,
    shape_index,
    animation_type="fade",
    trigger="on_click",
    duration=0.5,
    delay=0,
    order=1,
    direction=None,
):
    """
    Add animation metadata to a presentation.

    This doesn't actually add animations (not supported by python-pptx),
    but stores the information for reference.

    Args:
        presentation: The presentation object
        slide_index: Index of the slide (0-based)
        shape_index: Index of the shape in the slide's shapes collection
        animation_type: Type of animation ('fade', 'wipe', 'fly', etc.)
        trigger: What triggers the animation ('on_click', 'with_previous', 'after_previous')
        duration: Duration of the animation in seconds
        delay: Delay before animation starts in seconds
        order: Order of the animation in the sequence
        direction: Direction for animations that support it (e.g., 'from_left', 'from_bottom')
    """
    # Initialize animations dict if it doesn't exist
    if not hasattr(presentation, "_animations"):
        presentation._animations = {}

    # Create the slide entry if it doesn't exist
    slide_key = f"slide_{slide_index}"
    if slide_key not in presentation._animations:
        presentation._animations[slide_key] = []

    # Add the animation data
    animation_data = {
        "shape_index": shape_index,
        "type": animation_type,
        "trigger": trigger,
        "duration": duration,
        "delay": delay,
        "order": order,
    }

    if direction:
        animation_data["direction"] = direction

    presentation._animations[slide_key].append(animation_data)

    # Also add to shape's alt text for direct reference
    try:
        slide = presentation.slides[slide_index]
        shape = slide.shapes[shape_index]

        animation_text = (
            f"ANIMATION: {animation_type.upper()}, "
            f"Trigger: {trigger}, Order: {order}, "
            f"Duration: {duration}s, Delay: {delay}s"
        )

        if direction:
            animation_text += f", Direction: {direction}"

        # Add to shape's alternative text
        shape._element.xpath(".//p:cNvPr")[0].set("descr", animation_text)
    except Exception:
        # If unable to add to shape, just keep in presentation metadata
        pass

    return animation_data


def generate_animation_instructions(presentation, output_file=None):
    """
    Generate a set of animation instructions based on metadata.

    This can be used to create a text file with instructions for
    manually adding animations in PowerPoint.

    Args:
        presentation: The presentation with animation metadata
        output_file: Optional file path to save instructions

    Returns:
        A string with human-readable animation instructions
    """
    if not hasattr(presentation, "_animations"):
        return "No animation metadata found in presentation."

    instructions = ["# Animation Instructions", ""]

    for slide_key, animations in presentation._animations.items():
        slide_index = int(slide_key.split("_")[1])
        instructions.append(f"## Slide {slide_index + 1}")

        # Sort animations by order
        sorted_animations = sorted(animations, key=lambda x: x["order"])

        for anim in sorted_animations:
            shape_index = anim["shape_index"]
            anim_type = anim["type"].upper()
            trigger = anim["trigger"].replace("_", " ").title()
            duration = anim["duration"]
            delay = anim["delay"]
            order = anim["order"]
            direction = anim.get("direction", "Default")

            # Try to get shape name or use index
            try:
                slide = presentation.slides[slide_index]
                shape = slide.shapes[shape_index]

                # Try to get text from shape if it's a text shape
                shape_name = ""
                try:
                    if hasattr(shape, "text_frame") and shape.text_frame.text:
                        shape_name = f" ('{shape.text_frame.text}')"
                except Exception:
                    pass

                instructions.append(
                    f"- Shape {shape_index}{shape_name}: Apply '{anim_type}' animation"
                )
                instructions.append(
                    f"  - Trigger: {trigger}, Order: {order}, Duration: {duration}s, "
                    f"Delay: {delay}s, Direction: {direction}"
                )
            except Exception:
                instructions.append(f"- Shape {shape_index}: Apply '{anim_type}' animation")
                instructions.append(
                    f"  - Trigger: {trigger}, Order: {order}, Duration: {duration}s, "
                    f"Delay: {delay}s, Direction: {direction}"
                )

        instructions.append("")  # Empty line between slides

    # Join instructions into a string
    instructions_text = "\n".join(instructions)

    # Save to file if specified
    if output_file:
        with open(output_file, "w") as f:
            f.write(instructions_text)

    return instructions_text


# ====================== TRANSITION METADATA ====================== #


def add_slide_transition(
    presentation, slide_index, transition_type="fade", duration=1.0, trigger="on_click", delay=0
):
    """
    Add transition metadata to a slide.

    This doesn't actually add transitions (not supported by python-pptx),
    but stores the information for reference.

    Args:
        presentation: The presentation object
        slide_index: Index of the slide (0-based)
        transition_type: Type of transition (e.g., 'fade', 'push', 'wipe')
        duration: Duration of the transition in seconds
        trigger: What triggers the transition ('on_click', 'after_time')
        delay: If trigger is 'after_time', the delay in seconds
    """
    # Initialize transitions dict if it doesn't exist
    if not hasattr(presentation, "_transitions"):
        presentation._transitions = {}

    # Add the transition data
    slide_key = f"slide_{slide_index}"
    presentation._transitions[slide_key] = {
        "type": transition_type,
        "duration": duration,
        "trigger": trigger,
        "delay": delay,
    }

    # Also add to slide notes for direct reference
    try:
        slide = presentation.slides[slide_index]

        if not slide.has_notes_slide:
            _notes = slide.notes_slide  # Access triggers creation as side-effect

        notes_text = slide.notes_slide.notes_text_frame.text
        transition_text = (
            f"TRANSITION: {transition_type.upper()}, Duration: {duration}s, Trigger: {trigger}"
        )

        if trigger == "after_time":
            transition_text += f", Delay: {delay}s"

        # Append to existing notes or set as new notes
        if notes_text:
            slide.notes_slide.notes_text_frame.text = f"{notes_text}\n\n{transition_text}"
        else:
            slide.notes_slide.notes_text_frame.text = transition_text
    except Exception:
        # If unable to add to notes, just keep in presentation metadata
        pass

    return presentation._transitions[slide_key]


def generate_transition_instructions(presentation, output_file=None):
    """
    Generate a set of transition instructions based on metadata.

    This can be used to create a text file with instructions for
    manually adding transitions in PowerPoint.

    Args:
        presentation: The presentation with transition metadata
        output_file: Optional file path to save instructions

    Returns:
        A string with human-readable transition instructions
    """
    if not hasattr(presentation, "_transitions"):
        return "No transition metadata found in presentation."

    instructions = ["# Transition Instructions", ""]

    for slide_key, transition in presentation._transitions.items():
        slide_index = int(slide_key.split("_")[1])
        transition_type = transition["type"].upper()
        duration = transition["duration"]
        trigger = transition["trigger"].replace("_", " ").title()
        delay = transition.get("delay", 0)

        instructions.append(f"## Slide {slide_index + 1}")
        instructions.append(f"- Apply '{transition_type}' transition")
        instructions.append(f"  - Duration: {duration}s, Trigger: {trigger}")

        if trigger == "After Time":
            instructions.append(f"  - Delay before advancing: {delay}s")

        instructions.append("")  # Empty line between slides

    # Join instructions into a string
    instructions_text = "\n".join(instructions)

    # Save to file if specified
    if output_file:
        with open(output_file, "w") as f:
            f.write(instructions_text)

    return instructions_text


# ====================== EXPORT METADATA ====================== #


def export_presentation_metadata(presentation, output_file):
    """
    Export all metadata (animations, transitions) to a JSON file.

    This can be used to document the intended animations and transitions
    for manual application in PowerPoint.

    Args:
        presentation: The presentation object
        output_file: File path to save the metadata
    """
    metadata = {
        "animations": getattr(presentation, "_animations", {}),
        "transitions": getattr(presentation, "_transitions", {}),
    }

    # Convert to JSON
    with open(output_file, "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def import_presentation_metadata(presentation, input_file):
    """
    Import metadata from a JSON file.

    This can be used to restore animation and transition metadata
    that was previously exported.

    Args:
        presentation: The presentation object
        input_file: File path to load the metadata from
    """
    with open(input_file) as f:
        metadata = json.load(f)

    if "animations" in metadata:
        presentation._animations = metadata["animations"]

    if "transitions" in metadata:
        presentation._transitions = metadata["transitions"]

    return metadata


# ====================== HELPER FUNCTIONS ====================== #


def add_animation_guide_slide(presentation):
    """
    Add a guide slide with instructions for adding animations manually.

    This slide explains that python-pptx doesn't support animations directly,
    but that metadata has been added to help with manual animation.

    Args:
        presentation: The presentation object

    Returns:
        The created slide
    """
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])  # Blank layout

    # Add title
    title_shape = slide.shapes.title
    title_shape.text = "Animation and Transition Instructions"
    title_shape.text_frame.paragraphs[0].font.size = Pt(40)
    title_shape.text_frame.paragraphs[0].font.bold = True
    title_shape.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER

    # Add instructions text box
    textbox = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(4))

    tf = textbox.text_frame
    tf.word_wrap = True

    p1 = tf.paragraphs[0]
    p1.text = "The python-pptx library does not directly support animations or transitions."
    p1.font.size = Pt(18)
    p1.font.bold = True

    p2 = tf.add_paragraph()
    p2.text = (
        "However, animation and transition instructions have been encoded in this presentation:"
    )
    p2.font.size = Pt(18)

    p3 = tf.add_paragraph()
    p3.text = "1. Shapes with animations have instructions in their alternative text (right-click a shape → Size and Properties → Alt Text)"
    p3.font.size = Pt(16)

    p4 = tf.add_paragraph()
    p4.text = "2. Slides with transitions have instructions in their notes (View → Notes)"
    p4.font.size = Pt(16)

    p5 = tf.add_paragraph()
    p5.text = "3. A complete set of instructions has also been generated in a separate file"
    p5.font.size = Pt(16)

    p6 = tf.add_paragraph()
    p6.text = "Please apply these animations and transitions manually to complete the presentation."
    p6.font.size = Pt(18)
    p6.font.bold = True
    p6.font.italic = True

    # Add a note to the slide
    if not slide.has_notes_slide:
        _notes = slide.notes_slide  # Access triggers creation as side-effect

    slide.notes_slide.notes_text_frame.text = (
        "This slide provides guidance for manually adding animations and transitions "
        "since python-pptx does not support these features directly."
    )

    return slide


def add_animation_instructions_to_notes(presentation):
    """
    Add animation and transition instructions to each slide's notes.

    This compiles all the animation and transition metadata and adds it
    to the respective slide's notes for easy reference.

    Args:
        presentation: The presentation object with animation and transition metadata
    """
    has_animations = hasattr(presentation, "_animations")
    has_transitions = hasattr(presentation, "_transitions")

    if not (has_animations or has_transitions):
        return False

    for i, slide in enumerate(presentation.slides):
        instructions = []
        slide_key = f"slide_{i}"

        # Add transition instructions if available
        if has_transitions and slide_key in presentation._transitions:
            transition = presentation._transitions[slide_key]
            transition_type = transition["type"].upper()
            duration = transition["duration"]
            trigger = transition["trigger"].replace("_", " ").title()
            delay = transition.get("delay", 0)

            instructions.append("TRANSITION INSTRUCTIONS:")
            instructions.append(f"- Apply '{transition_type}' transition")
            instructions.append(f"- Duration: {duration}s, Trigger: {trigger}")

            if trigger == "After Time":
                instructions.append(f"- Delay before advancing: {delay}s")

            instructions.append("")

        # Add animation instructions if available
        if has_animations and slide_key in presentation._animations:
            animations = presentation._animations[slide_key]

            # Sort animations by order
            sorted_animations = sorted(animations, key=lambda x: x["order"])

            instructions.append("ANIMATION INSTRUCTIONS:")

            for anim in sorted_animations:
                shape_index = anim["shape_index"]
                anim_type = anim["type"].upper()
                trigger = anim["trigger"].replace("_", " ").title()
                duration = anim["duration"]
                delay = anim["delay"]
                order = anim["order"]
                direction = anim.get("direction", "Default")

                # Try to get shape name or use index
                shape_name = ""
                try:
                    shape = slide.shapes[shape_index]
                    if hasattr(shape, "text_frame") and shape.text_frame.text:
                        shape_name = f" ('{shape.text_frame.text}')"
                except Exception:
                    pass

                instructions.append(
                    f"- Shape {shape_index}{shape_name}: Apply '{anim_type}' animation"
                )
                instructions.append(
                    f"  - Trigger: {trigger}, Order: {order}, Duration: {duration}s, "
                    f"Delay: {delay}s, Direction: {direction}"
                )

        # Add the instructions to the slide notes
        if instructions:
            if not slide.has_notes_slide:
                _notes = slide.notes_slide  # Access triggers creation as side-effect

            existing_notes = slide.notes_slide.notes_text_frame.text

            # Combine existing notes with new instructions
            combined_notes = existing_notes + "\n\n" if existing_notes else ""
            combined_notes += "\n".join(instructions)

            slide.notes_slide.notes_text_frame.text = combined_notes

    return True
