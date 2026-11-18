"""Regression: metric_summary must tolerate non-dict KPI elements.

`content["kpis"]` comes straight from LLM/user input with no per-element type
check. A non-dict element (str, None, number) previously raised AttributeError
on `kpi.get(...)` and aborted the whole slide/deck render. The renderer must
skip the malformed element and still render the valid KPIs.
"""

from __future__ import annotations

import pytest
from pptx import Presentation

from src.services.template_renderer import render_slide


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
    texts = []
    for shape in slide.shapes:
        if hasattr(shape, "text_frame"):
            text = shape.text_frame.text.strip()
            if text:
                texts.append(text)
    return texts


def test_metric_summary_tolerates_non_dict_kpi(brand, styling_rules):
    """A non-dict KPI element is skipped; valid KPIs still render."""
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="metric_summary",
        content={
            "title": "Key Metrics",
            # First element is a bare string (malformed LLM output). Before the
            # guard this raised AttributeError: 'str' object has no attribute
            # 'get' and aborted the render.
            "kpis": ["oops", {"name": "Revenue", "value": "1M"}],
        },
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    assert any("Revenue" in t for t in texts)
    assert any("1M" in t for t in texts)


def test_metric_summary_tolerates_non_list_kpis(brand, styling_rules):
    """A present-but-non-list `kpis` value must not crash the render.

    HOLE 1: `kpis = content.get("kpis", [])` returns the raw value; the
    `if kpis` max_cols guard swallows None so a non-list still reaches
    `enumerate(kpis)`, which raises TypeError and aborts the whole deck.
    """
    prs = Presentation()
    # `{"kpis": None}` — enumerate(None) raised `TypeError: 'NoneType' object
    # is not iterable` before the coercion guard. Render must still succeed.
    slide = render_slide(
        prs,
        layout_type="metric_summary",
        content={"title": "Key Metrics", "kpis": None},
        brand=brand,
        styling_rules=styling_rules,
    )
    # Title still renders; no KPI cards, but no crash.
    assert any("Key Metrics" in t for t in _text_shapes(slide))

    # A dict value (`{"kpis": {...}}`) enumerates its KEYS as str elements —
    # the per-element dict guard skips them, but enumerate itself must not
    # crash. This also exercises the non-list branch.
    prs2 = Presentation()
    slide2 = render_slide(
        prs2,
        layout_type="metric_summary",
        content={"title": "Key Metrics", "kpis": {"a": 1}},
        brand=brand,
        styling_rules=styling_rules,
    )
    assert any("Key Metrics" in t for t in _text_shapes(slide2))


def test_metric_summary_tolerates_non_str_kpi_name(brand, styling_rules):
    """A dict KPI with a non-str `name` must render, not crash.

    HOLE 2: `p_name.text = name` where `name = kpi.get("name", "")`. Unlike
    `value`/`delta`, `name` was not `str(...)`-wrapped, so a numeric name
    (ordinary LLM output) raised `TypeError: expected string or bytes-like
    object, got 'int'` and aborted the whole deck.
    """
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="metric_summary",
        content={"kpis": [{"name": 123, "value": "x"}]},
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _text_shapes(slide)
    # The numeric name renders as its string form.
    assert any("123" in t for t in texts)
    assert any("x" in t for t in texts)
