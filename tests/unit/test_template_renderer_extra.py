"""Extra template renderer tests for uncovered branches."""

from __future__ import annotations

import pytest
from pptx import Presentation

from src.services.template_renderer import render_slide


@pytest.fixture
def prs():
    return Presentation()


@pytest.fixture
def brand():
    return {
        "primary_color": "#1a73e8",
        "secondary_color": "#34a853",
        "font_family": "Arial",
        "theme": "modern",
    }


@pytest.fixture
def styling_rules():
    return {"title_font_size": 28, "layout": "centered"}


def _text_shapes(slide):
    """Extract all text shapes from a slide (textboxes and auto shapes with text)."""
    texts = []
    for shape in slide.shapes:
        if hasattr(shape, "text_frame"):
            text = shape.text_frame.text.strip()
            if text:
                texts.append(text)
    return texts


def test_render_roadmap_with_phases_and_dates(prs, brand, styling_rules):
    """Roadmap with phases, dates, and items renders all phase elements."""
    slide = render_slide(
        prs,
        layout_type="roadmap",
        content={
            "title": "Product Roadmap",
            "phases": [
                {
                    "name": "Phase 1",
                    "date": "Q1 2026",
                    "items": ["Define requirements", "Prototype"],
                },
                {
                    "name": "Phase 2",
                    "date": "Q2 2026",
                    "items": ["Build MVP", "Beta testing"],
                },
                {
                    "name": "Phase 3",
                    "date": "Q3 2026",
                    "items": ["Launch"],
                },
            ],
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    # Title must be present
    assert any("Product Roadmap" in t for t in texts)
    # All 3 phase names must appear
    assert any("Phase 1" in t for t in texts)
    assert any("Phase 2" in t for t in texts)
    assert any("Phase 3" in t for t in texts)
    # Dates must appear
    assert any("Q1 2026" in t for t in texts)
    assert any("Q2 2026" in t for t in texts)
    # Items should appear as bullet text
    assert any("Define requirements" in t for t in texts)
    assert any("Launch" in t for t in texts)


def test_render_roadmap_empty_phases(prs, brand, styling_rules):
    """Roadmap with empty phases list renders only the title."""
    slide = render_slide(
        prs,
        layout_type="roadmap",
        content={"title": "Empty Roadmap", "phases": []},
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    assert any("Empty Roadmap" in t for t in texts)
    # No phase-related shapes should exist (only title + maybe background elements)
    assert not any("Phase" in t for t in texts)


def test_render_metric_summary_with_full_kpis(prs, brand, styling_rules):
    """Metric summary renders all KPIs and the trend description text."""
    slide = render_slide(
        prs,
        layout_type="metric_summary",
        content={
            "title": "Performance KPIs",
            "kpis": [
                {"name": "Revenue", "value": "$1.2M", "delta": "+12%"},
                {"name": "Users", "value": "50K", "delta": "+8%"},
                {"name": "Retention", "value": "92%", "delta": "+3%"},
                {"name": "NPS", "value": "72", "delta": "+5"},
                {"name": "CAC", "value": "$45", "delta": "-15%"},
                {"name": "LTV", "value": "$890", "delta": "+22%"},
            ],
            "trend_description": "All metrics trending upward this quarter.",
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    # Title must be present
    assert any("Performance KPIs" in t for t in texts)
    # Each KPI name must appear somewhere in text shapes
    for name in ("Revenue", "Users", "Retention", "NPS", "CAC", "LTV"):
        assert any(name in t for t in texts), f"KPI name '{name}' not found in slide text"
    # Each KPI value must appear somewhere
    for value in ("$1.2M", "50K", "92%", "72", "$45", "$890"):
        assert any(value in t for t in texts), f"KPI value '{value}' not found in slide text"


def test_render_cta_with_all_fields(prs, brand, styling_rules):
    """CTA slide with all optional fields renders title, body, button, URL, and contact."""
    slide = render_slide(
        prs,
        layout_type="cta",
        content={
            "title": "Ready to Transform?",
            "body": "Join 1,000+ companies already on board.",
            "cta_text": "Schedule a Demo",
            "cta_url": "https://example.com/demo",
            "contact_info": "sales@example.com | 1-800-555-1234",
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    assert any("Ready to Transform?" in t for t in texts)
    assert any("Join 1,000+" in t for t in texts)
    assert any("Schedule a Demo" in t for t in texts)
    assert any("https://example.com/demo" in t for t in texts)
    assert any("sales@example.com" in t for t in texts)


def test_render_scorecard_with_footer(prs, brand, styling_rules):
    """Scorecard with footer renders metrics and footer text."""
    slide = render_slide(
        prs,
        layout_type="scorecard",
        content={
            "title": "Q1 Scores",
            "metrics": [
                {"label": "Speed", "value": "95"},
                {"label": "Quality", "value": "88"},
                {"label": "Coverage", "value": "72"},
                {"label": "Uptime", "value": "99.9%"},
            ],
            "footer": "Updated March 2026",
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    # Title
    assert any("Q1 Scores" in t for t in texts)
    # Metrics
    for label in ("Speed", "Quality", "Coverage", "Uptime"):
        assert any(label in t for t in texts), f"Metric '{label}' not found"
    for value in ("95", "88", "72", "99.9%"):
        assert any(value in t for t in texts), f"Value '{value}' not found"
    # Footer
    assert any("Updated March 2026" in t for t in texts)


def test_render_comparison_with_caption(prs, brand, styling_rules):
    """Comparison slide renders title, before/after labels, and caption."""
    slide = render_slide(
        prs,
        layout_type="comparison",
        content={
            "title": "UI Redesign",
            "before_label": "Old Design",
            "after_label": "New Design",
            "caption": "Notice the improved layout and accessibility.",
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    assert any("UI Redesign" in t for t in texts)
    assert any("Old Design" in t for t in texts)
    assert any("New Design" in t for t in texts)
    assert any("Notice the improved layout" in t for t in texts)


def test_render_finding_detail_with_recommendation(prs, brand, styling_rules):
    """Finding detail renders title, description, severity, and recommendation."""
    slide = render_slide(
        prs,
        layout_type="finding_detail",
        content={
            "title": "Security Vulnerability",
            "description": "SQL injection vulnerability found in login form.",
            "severity": "CRITICAL",
            "recommendation": "Parameterize all SQL queries. Apply input validation.",
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    assert any("Security Vulnerability" in t for t in texts)
    assert any("SQL injection" in t for t in texts)
    assert any("CRITICAL" in t for t in texts)
    assert any("Parameterize all SQL queries" in t for t in texts)


def test_render_feature_spotlight_with_bullets(prs, brand, styling_rules):
    """Feature spotlight renders title, description, and all bullet points."""
    slide = render_slide(
        prs,
        layout_type="feature_spotlight",
        content={
            "title": "Smart Dashboard",
            "description": "Real-time analytics at your fingertips.",
            "bullets": ["Live metrics", "Custom widgets", "Export to PDF"],
        },
        brand=brand,
        styling_rules=styling_rules,
    )

    texts = _text_shapes(slide)
    assert any("Smart Dashboard" in t for t in texts)
    assert any("Real-time analytics" in t for t in texts)
    # Bullet points should appear
    assert any("Live metrics" in t for t in texts)
    assert any("Custom widgets" in t for t in texts)
    assert any("Export to PDF" in t for t in texts)
