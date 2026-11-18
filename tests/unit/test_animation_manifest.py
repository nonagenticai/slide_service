"""Tests for animation manifest helpers."""

from __future__ import annotations

from src.services.animation_manifest import (
    create_animation_entry,
    create_transition_entry,
    export_metadata,
)


def test_create_animation_entry():
    """Test creating an animation entry."""
    entry = create_animation_entry(
        shape_index=0,
        animation_type="fade",
        trigger="on_click",
        duration=0.5,
        delay=0,
        order=1,
    )

    assert entry["shape_index"] == 0
    assert entry["type"] == "fade"
    assert entry["trigger"] == "on_click"
    assert entry["duration"] == 0.5
    assert "direction" not in entry


def test_create_animation_entry_with_direction():
    """Test animation entry with direction."""
    entry = create_animation_entry(
        shape_index=1,
        animation_type="fly",
        direction="from_left",
    )

    assert entry["direction"] == "from_left"


def test_create_transition_entry():
    """Test creating a transition entry."""
    entry = create_transition_entry(transition_type="wipe", duration=1.0)

    assert entry["type"] == "wipe"
    assert entry["duration_seconds"] == 1.0


def test_export_metadata():
    """Test exporting metadata dict."""
    animations = {"slide_0": [create_animation_entry(0)]}
    transitions = {"slide_0": create_transition_entry()}

    metadata = export_metadata(animations, transitions)

    assert "animations" in metadata
    assert "transitions" in metadata
    assert "slide_0" in metadata["animations"]
