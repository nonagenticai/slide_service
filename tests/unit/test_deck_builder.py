"""Unit tests for deck builder canvas sizing."""

from __future__ import annotations

from pptx import Presentation
from pptx.util import Inches

from src.config import Settings
from src.services.deck_builder import DeckBuilder


def test_apply_aspect_ratio_sets_widescreen_canvas():
    builder = DeckBuilder(Settings())
    prs = Presentation()

    builder._apply_aspect_ratio(prs, "16:9")

    assert prs.slide_width == Inches(13.333)
    assert prs.slide_height == Inches(7.5)


def test_apply_aspect_ratio_sets_standard_canvas():
    builder = DeckBuilder(Settings())
    prs = Presentation()

    builder._apply_aspect_ratio(prs, "4:3")

    assert prs.slide_width == Inches(10)
    assert prs.slide_height == Inches(7.5)


def test_apply_slide_asset_contract_resolves_explicit_asset_urls():
    builder = DeckBuilder(Settings())

    content, asset_urls, asset_placements = builder._apply_slide_asset_contract(
        {
            "content": {"title": "Hero"},
            "asset_urls": {"image_url": "asset:hero-image"},
            "asset_placements": {"image_url": {"mode": "full_bleed", "fit": "contain"}},
        },
        {"hero-image": "https://cdn.example.com/hero.png"},
    )

    assert content["image_url"] == "https://cdn.example.com/hero.png"
    assert asset_urls == {"image_url": "https://cdn.example.com/hero.png"}
    assert content["asset_placements"]["image_url"]["fit"] == "contain"
    assert asset_placements["image_url"]["mode"] == "full_bleed"


def test_apply_slide_asset_contract_resolves_legacy_image_slots():
    builder = DeckBuilder(Settings())

    content, asset_urls, asset_placements = builder._apply_slide_asset_contract(
        {
            "content": {
                "title": "Finding",
                "screenshot_url": "asset://evidence-shot",
            }
        },
        {"evidence-shot": "https://cdn.example.com/evidence.png"},
    )

    assert content["screenshot_url"] == "https://cdn.example.com/evidence.png"
    assert asset_urls["screenshot_url"] == "https://cdn.example.com/evidence.png"
    assert asset_placements == {}
