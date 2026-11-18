"""Tests for DeckBuilder helper methods and asset handling."""

from __future__ import annotations

from src.config import Settings
from src.services.deck_builder import DeckBuilder


def _builder() -> DeckBuilder:
    return DeckBuilder(Settings())


def test_build_asset_catalog_basic():
    """Test building asset catalog from image assets."""
    builder = _builder()
    catalog = builder._build_asset_catalog(
        [
            {"asset_id": "hero", "image_url": "https://example.com/hero.png"},
            {"asset_id": "logo", "image_url": "https://example.com/logo.png"},
        ]
    )
    assert catalog == {
        "hero": "https://example.com/hero.png",
        "logo": "https://example.com/logo.png",
    }


def test_build_asset_catalog_skips_invalid():
    """Test that invalid assets are skipped."""
    builder = _builder()
    catalog = builder._build_asset_catalog(
        [
            {"asset_id": "", "image_url": "https://example.com/a.png"},
            {"asset_id": "good", "image_url": ""},
            "not-a-dict",
            {"asset_id": "valid", "image_url": "https://example.com/valid.png"},
        ]
    )
    assert catalog == {"valid": "https://example.com/valid.png"}


def test_build_asset_catalog_non_list():
    """Test catalog builder handles non-list input."""
    builder = _builder()
    catalog = builder._build_asset_catalog("not a list")
    assert catalog == {}


def test_resolve_asset_reference_plain_url():
    """Plain URL passes through."""
    builder = _builder()
    result = builder._resolve_asset_reference("https://example.com/img.png", {})
    assert result == "https://example.com/img.png"


def test_resolve_asset_reference_asset_prefix():
    """asset:// prefix is resolved from catalog."""
    builder = _builder()
    catalog = {"my-image": "https://cdn.example.com/image.png"}
    result = builder._resolve_asset_reference("asset://my-image", catalog)
    assert result == "https://cdn.example.com/image.png"


def test_resolve_asset_reference_asset_colon():
    """asset: prefix (no slash) is resolved from catalog."""
    builder = _builder()
    catalog = {"shot-1": "https://cdn.example.com/shot.png"}
    result = builder._resolve_asset_reference("asset:shot-1", catalog)
    assert result == "https://cdn.example.com/shot.png"


def test_resolve_asset_reference_empty():
    """Empty reference returns empty string."""
    builder = _builder()
    assert builder._resolve_asset_reference("", {}) == ""
    assert builder._resolve_asset_reference(None, {}) == ""


def test_collect_image_refs_from_dict():
    """Test collecting image refs from nested dict."""
    builder = _builder()
    refs = builder._collect_image_refs(
        {
            "title": "Hello",
            "image_url": "https://example.com/hero.png",
            "screenshot": "https://example.com/shot.png",
        }
    )
    assert "https://example.com/hero.png" in refs
    assert "https://example.com/shot.png" in refs
    assert "Hello" not in refs


def test_collect_image_refs_from_list():
    """Test collecting image refs from list values."""
    builder = _builder()
    refs = builder._collect_image_refs([{"image_url": "a.png"}, {"image_url": "b.png"}])
    assert "a.png" in refs
    assert "b.png" in refs


def test_collect_image_refs_empty():
    """Test collecting image refs from empty/non-image content."""
    builder = _builder()
    refs = builder._collect_image_refs({"title": "Hello", "body": "world"})
    assert refs == []


def test_collect_image_refs_skips_empty_strings():
    """Empty string values are not collected."""
    builder = _builder()
    refs = builder._collect_image_refs({"image_url": ""})
    assert refs == []


def test_apply_slide_asset_contract_no_image_slots():
    """Non-image content passes through without asset mapping."""
    builder = _builder()
    content, asset_urls, placements = builder._apply_slide_asset_contract(
        {"content": {"title": "Hello", "body": "World"}},
        {},
    )
    assert content["title"] == "Hello"
    assert asset_urls == {}
    assert placements == {}


def test_apply_slide_asset_contract_with_placements():
    """Asset placements are included in content."""
    builder = _builder()
    content, _asset_urls, placements = builder._apply_slide_asset_contract(
        {
            "content": {"title": "Test"},
            "asset_placements": {"image_url": {"mode": "full_bleed", "fit": "contain"}},
        },
        {},
    )
    assert "asset_placements" in content
    assert placements["image_url"]["mode"] == "full_bleed"


def test_collect_request_image_assets():
    """Test collecting external image assets from request."""
    builder = _builder()
    image_assets = [
        {"asset_id": "a1", "image_url": "https://example.com/a.png"},
    ]
    slides = [
        {"content": {"image_url": "https://example.com/b.png"}},
    ]
    result = builder._collect_request_image_assets(image_assets, slides)
    urls = [a["image_url"] for a in result]
    assert "https://example.com/a.png" in urls
    assert "https://example.com/b.png" in urls


def test_collect_request_image_assets_deduplicates():
    """Duplicate URLs are not repeated."""
    builder = _builder()
    image_assets = [
        {"asset_id": "a1", "image_url": "https://example.com/same.png"},
    ]
    slides = [
        {"content": {"image_url": "https://example.com/same.png"}},
    ]
    result = builder._collect_request_image_assets(image_assets, slides)
    urls = [a["image_url"] for a in result]
    assert urls.count("https://example.com/same.png") == 1


def test_apply_aspect_ratio_unknown_fallback():
    """Unknown aspect ratio falls back to 16:9."""
    from pptx import Presentation
    from pptx.util import Inches

    builder = _builder()
    prs = Presentation()
    builder._apply_aspect_ratio(prs, "weird:ratio")
    assert prs.slide_width == Inches(13.333)
    assert prs.slide_height == Inches(7.5)
