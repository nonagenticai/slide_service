"""Animation and transition metadata -- refactored from animation_transition_helpers.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NotRequired, TypedDict


class AnimationEntry(TypedDict):
    """One shape-level animation record in the deck manifest.

    Every key is produced by :func:`create_animation_entry` from a typed
    parameter, so the shape is fully known at author time -- there is no JSON
    boundary here and therefore no reason for ``dict[str, Any]``.
    """

    shape_index: int
    type: str
    trigger: str
    duration: float
    delay: float
    order: int
    direction: NotRequired[str]


class TransitionEntry(TypedDict):
    """One slide-level transition record in the deck manifest."""

    type: str
    duration_seconds: float


class AnimationMetadata(TypedDict):
    """The exported animation/transition metadata document."""

    animations: dict[str, list[AnimationEntry]]
    transitions: dict[str, TransitionEntry]


def create_animation_entry(
    shape_index: int,
    animation_type: str = "fade",
    trigger: str = "on_click",
    duration: float = 0.5,
    delay: float = 0,
    order: int = 1,
    direction: str | None = None,
) -> AnimationEntry:
    """Create an animation metadata entry."""
    entry: AnimationEntry = {
        "shape_index": shape_index,
        "type": animation_type,
        "trigger": trigger,
        "duration": duration,
        "delay": delay,
        "order": order,
    }
    if direction:
        entry["direction"] = direction
    return entry


def create_transition_entry(
    transition_type: str = "fade",
    duration: float = 0.5,
) -> TransitionEntry:
    """Create a transition metadata entry for the manifest."""
    return {
        "type": transition_type,
        "duration_seconds": duration,
    }


def export_metadata(
    animations: dict[str, list[AnimationEntry]],
    transitions: dict[str, TransitionEntry],
    output_file: str | None = None,
) -> AnimationMetadata:
    """Export all metadata to a dict, optionally write to file."""
    metadata: AnimationMetadata = {
        "animations": animations,
        "transitions": transitions,
    }
    if output_file:
        with Path(output_file).open("w") as f:
            json.dump(metadata, f, indent=2)
    return metadata
