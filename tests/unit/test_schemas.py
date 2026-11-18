"""Tests for API schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas import (
    AssetPlacement,
    BrandConfig,
    ContentShaperConfig,
    DeckCreateRequest,
    DeckJobResponse,
    DeckStatusResponse,
    SlideContent,
    TemplateCreateRequest,
    TemplateResponse,
    TemplateUpdateRequest,
    TimingConfig,
)


def test_brand_config_defaults():
    """Test BrandConfig has sensible defaults."""
    brand = BrandConfig()
    assert brand.primary_color == "#1a73e8"
    assert brand.secondary_color == "#34a853"
    assert brand.font_family == "Arial"
    assert brand.theme == "modern"
    assert brand.logo_url is None


def test_content_shaper_config_defaults():
    """Test ContentShaperConfig defaults to disabled."""
    config = ContentShaperConfig()
    assert config.enabled is False
    assert config.summarize is True
    assert config.tone == "professional"


def test_timing_config_defaults():
    """Test TimingConfig defaults."""
    timing = TimingConfig()
    assert timing.target_duration_seconds == 120.0
    assert timing.default_slide_duration_seconds == 15.0


def test_slide_content():
    """Test SlideContent model."""
    slide = SlideContent(
        template_id="test-id",
        content={"title": "Hello"},
        asset_urls={"image_url": "https://example.com/hero.png"},
        asset_placements={"image_url": AssetPlacement(mode="full_bleed", fit="contain")},
    )
    assert slide.template_id == "test-id"
    assert slide.content["title"] == "Hello"
    assert slide.asset_urls["image_url"] == "https://example.com/hero.png"
    assert slide.asset_placements["image_url"].mode == "full_bleed"
    assert slide.asset_placements["image_url"].fit == "contain"


def test_deck_create_request():
    """Test DeckCreateRequest with all fields."""
    req = DeckCreateRequest(
        title="My Deck",
        slides=[
            SlideContent(template_id="t1", content={"title": "Slide 1"}),
        ],
    )
    assert req.title == "My Deck"
    assert len(req.slides) == 1
    assert req.aspect_ratio == "16:9"
    assert req.content_shaper.enabled is False


def test_deck_job_response():
    """Test DeckJobResponse model."""
    resp = DeckJobResponse(job_id="abc", status="processing")
    assert resp.job_id == "abc"
    assert resp.status == "processing"


def test_deck_status_response():
    """Test DeckStatusResponse model."""
    resp = DeckStatusResponse(
        job_id="abc",
        status="completed",
        slide_count=5,
    )
    assert resp.slide_count == 5
    assert resp.error is None


def test_template_create_request():
    """Test TemplateCreateRequest model."""
    req = TemplateCreateRequest(
        name="test",
        display_name="Test Template",
        category="data",
        layout_type="scorecard",
        slot_definitions={"title": {"type": "text"}},
    )
    assert req.name == "test"
    assert req.default_timing_hint_seconds == 10.0


@pytest.mark.parametrize("bad_value", [0.0, -1.0])
def test_template_create_request_rejects_non_positive_timing_hint(bad_value: float):
    """A zero or negative default_timing_hint_seconds is rejected at the schema edge."""
    with pytest.raises(ValidationError):
        TemplateCreateRequest(
            name="test",
            display_name="Test Template",
            category="data",
            layout_type="scorecard",
            slot_definitions={"title": {"type": "text"}},
            default_timing_hint_seconds=bad_value,
        )


@pytest.mark.parametrize("bad_value", [0.0, -1.0])
def test_template_update_request_rejects_non_positive_timing_hint(bad_value: float):
    """The optional update field carries the same > 0 bound."""
    with pytest.raises(ValidationError):
        TemplateUpdateRequest(default_timing_hint_seconds=bad_value)


def test_template_update_request_allows_omitted_timing_hint():
    """Omitting the field still means 'no change', not a validation error."""
    req = TemplateUpdateRequest(display_name="Renamed")
    assert req.default_timing_hint_seconds is None


def test_template_response():
    """Test TemplateResponse model."""
    resp = TemplateResponse(
        id="t1",
        name="title",
        display_name="Title Slide",
        category="narrative",
        layout_type="title",
        slot_definitions={"title": {"type": "text"}},
        styling_rules={"layout": "centered"},
        is_system=True,
    )
    assert resp.is_system is True
    assert resp.is_active is True
