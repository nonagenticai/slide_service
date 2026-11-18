"""Tests for image layout placement and fit resolution."""

from __future__ import annotations

from src.services.image_layout import (
    IMAGE_FIT_CONTAIN,
    IMAGE_FIT_COVER,
    IMAGE_PLACEMENT_FRAME,
    IMAGE_PLACEMENT_FULL_BLEED,
    normalize_image_fit,
    normalize_image_placement,
    resolve_asset_layout,
    resolve_image_fit,
    resolve_image_placement,
)


def test_normalize_image_placement_aliases():
    assert normalize_image_placement("framed") == IMAGE_PLACEMENT_FRAME
    assert normalize_image_placement("frame") == IMAGE_PLACEMENT_FRAME
    assert normalize_image_placement("panel") == IMAGE_PLACEMENT_FRAME
    assert normalize_image_placement("card") == IMAGE_PLACEMENT_FRAME
    assert normalize_image_placement("full_bleed") == IMAGE_PLACEMENT_FULL_BLEED
    assert normalize_image_placement("bleed") == IMAGE_PLACEMENT_FULL_BLEED
    assert normalize_image_placement("edge_to_edge") == IMAGE_PLACEMENT_FULL_BLEED
    assert normalize_image_placement("edge-to-edge") == IMAGE_PLACEMENT_FULL_BLEED


def test_normalize_image_placement_empty():
    assert normalize_image_placement("") == IMAGE_PLACEMENT_FRAME
    assert normalize_image_placement(None) == IMAGE_PLACEMENT_FRAME


def test_normalize_image_fit_aliases():
    assert normalize_image_fit("cover") == IMAGE_FIT_COVER
    assert normalize_image_fit("crop") == IMAGE_FIT_COVER
    assert normalize_image_fit("fill") == IMAGE_FIT_COVER
    assert normalize_image_fit("contain") == IMAGE_FIT_CONTAIN
    assert normalize_image_fit("fit") == IMAGE_FIT_CONTAIN
    assert normalize_image_fit("fit_inside") == IMAGE_FIT_CONTAIN
    assert normalize_image_fit("inside") == IMAGE_FIT_CONTAIN


def test_normalize_image_fit_empty():
    assert normalize_image_fit("") == IMAGE_FIT_COVER
    assert normalize_image_fit(None) == IMAGE_FIT_COVER


def test_resolve_image_placement_from_content():
    content = {"image_placement": "full_bleed"}
    result = resolve_image_placement(content, {})
    assert result == IMAGE_PLACEMENT_FULL_BLEED


def test_resolve_image_placement_from_styling_rules():
    result = resolve_image_placement({}, {"image_placement": "panel"})
    assert result == IMAGE_PLACEMENT_FRAME


def test_resolve_image_placement_fallback():
    result = resolve_image_placement({}, {})
    assert result == IMAGE_PLACEMENT_FRAME


def test_resolve_image_fit_from_content():
    content = {"image_fit": "contain"}
    result = resolve_image_fit(content, {})
    assert result == IMAGE_FIT_CONTAIN


def test_resolve_image_fit_fallback():
    result = resolve_image_fit({}, {})
    assert result == IMAGE_FIT_COVER


def test_resolve_asset_layout_explicit_placements():
    content = {
        "asset_placements": {
            "image_url": {"mode": "full_bleed", "fit": "contain"},
        },
    }
    placement, fit = resolve_asset_layout(content, {}, slot_name="image_url")
    assert placement == IMAGE_PLACEMENT_FULL_BLEED
    assert fit == IMAGE_FIT_CONTAIN


def test_resolve_asset_layout_fallback():
    placement, fit = resolve_asset_layout({}, {}, slot_name="image_url")
    assert placement == IMAGE_PLACEMENT_FRAME
    assert fit == IMAGE_FIT_COVER
