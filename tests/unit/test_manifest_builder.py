"""Tests for manifest builder."""

from __future__ import annotations

from src.services.manifest_builder import build_manifest


def test_build_manifest_basic():
    """Test basic manifest generation."""
    slides = [
        {
            "template_name": "title",
            "template_version_id": "v1",
            "content": {"title": "Hello", "subtitle": "World"},
            "asset_urls": {"hero_image_url": "https://example.com/hero.png"},
            "asset_placements": {"hero_image_url": {"mode": "full_bleed", "fit": "cover"}},
            "timing_hint_seconds": 8.0,
            "transition": {"type": "fade", "duration_seconds": 0.5},
        },
        {
            "template_name": "agenda",
            "template_version_id": "v2",
            "content": {"title": "Agenda", "items": ["Item 1"]},
            "timing_hint_seconds": 10.0,
        },
    ]

    manifest = build_manifest(
        job_id="test-job-123",
        title="Test Deck",
        aspect_ratio="16:9",
        brand={"primary_color": "#1a73e8"},
        slides=slides,
    )

    assert manifest["manifest_version"] == "1.0"
    assert manifest["deck_id"] == "test-job-123"
    assert manifest["title"] == "Test Deck"
    assert manifest["aspect_ratio"] == "16:9"
    assert manifest["slide_count"] == 2
    assert manifest["total_estimated_duration_seconds"] == 18.0
    assert len(manifest["slides"]) == 2
    assert manifest["slides"][0]["index"] == 0
    assert manifest["slides"][0]["template_name"] == "title"
    assert manifest["slides"][0]["asset_urls"]["hero_image_url"] == "https://example.com/hero.png"
    assert manifest["slides"][0]["asset_placements"]["hero_image_url"]["mode"] == "full_bleed"
    assert manifest["slides"][1]["index"] == 1


def test_build_manifest_with_timing():
    """Test manifest respects timing config."""
    slides = [
        {"template_name": "title", "content": {}},
    ]

    manifest = build_manifest(
        job_id="test-job",
        title="Test",
        aspect_ratio="16:9",
        brand={},
        slides=slides,
        timing={"default_slide_duration_seconds": 20.0},
    )

    assert manifest["slides"][0]["timing_hint_seconds"] == 20.0
    assert manifest["total_estimated_duration_seconds"] == 20.0


def test_build_manifest_assets():
    """Test manifest contains browser-safe deck asset metadata."""
    manifest = build_manifest(
        job_id="abc-123",
        title="Test",
        aspect_ratio="4:3",
        brand={},
        slides=[{"template_name": "title", "content": {}}],
    )

    assert manifest["assets"]["pptx"]["storage_path"] == "decks/abc-123/deck.pptx"
    assert manifest["assets"]["pptx"]["download_url"] == "/api/decks/abc-123/pptx"
    assert manifest["assets"]["pdf"]["storage_path"] == "decks/abc-123/deck.pdf"
    assert manifest["assets"]["pdf"]["download_url"] == "/api/decks/abc-123/pdf"
    assert manifest["assets"]["manifest"]["storage_path"] == "decks/abc-123/manifest.json"
    assert manifest["assets"]["manifest"]["download_url"] == "/api/decks/abc-123/manifest"


def test_build_manifest_empty_slides():
    """Test manifest with no slides."""
    manifest = build_manifest(
        job_id="empty",
        title="Empty",
        aspect_ratio="16:9",
        brand={},
        slides=[],
    )

    assert manifest["slide_count"] == 0
    assert manifest["total_estimated_duration_seconds"] == 0.0
    assert manifest["slides"] == []
