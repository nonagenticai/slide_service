"""Regression: dropping malformed KPIs must never be SILENT.

Guarding `content["kpis"]` against non-dict elements is only half the fix. A
bare ``continue`` turns a fully-malformed input (``["revenue", "churn"]`` — the
exact shape an LLM emits when it forgets the object wrapper) into a slide that
renders *successfully* with an empty metric panel and no signal anywhere: no
log line, no marker, no exception. Downstream that is indistinguishable from
"the deck legitimately had no metrics", which is this fleet's
skip-and-continue-becomes-silent-green defect class.

So: every dropped entry must produce a WARNING, and a slide that dropped
entries must carry a visible degraded marker.
"""

from __future__ import annotations

import logging

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


def _texts(slide):
    out = []
    for shape in slide.shapes:
        if hasattr(shape, "text_frame"):
            text = shape.text_frame.text.strip()
            if text:
                out.append(text)
    return out


def test_fully_malformed_kpi_list_is_not_a_silent_empty_slide(brand, styling_rules, caplog):
    """A list where EVERY element is malformed must not render as a clean deck.

    This is the load-bearing case. The whole payload is a list of bare strings;
    every element is skipped, so the metric panel is empty. Rendering that as an
    ordinary, successful, blank-but-tidy slide is the bug: the caller gets no
    way to tell "no metrics were requested" from "all metrics were destroyed".
    """
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        slide = render_slide(
            prs,
            layout_type="metric_summary",
            content={"title": "Key Metrics", "kpis": ["revenue", "churn", "arpu"]},
            brand=brand,
            styling_rules=styling_rules,
        )

    texts = _texts(slide)

    # 1. The render survived and the rest of the slide is intact.
    assert any("Key Metrics" in t for t in texts)

    # 2. Every dropped entry is logged, with a count the caller can alert on.
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "dropping 3 malformed KPIs emitted no warning at all"
    assert any("3" in w for w in warnings), (
        f"no warning reports how many KPIs were dropped; got {warnings!r}"
    )

    # 3. The slide itself carries a visible degraded marker, so an empty metric
    #    panel cannot be mistaken for a correctly-rendered empty deck.
    assert any("could not be displayed" in t for t in texts), (
        f"fully-malformed KPI list rendered as a silent, perfect empty slide; texts={texts!r}"
    )


def test_partially_malformed_kpi_list_marks_the_dropped_entries(brand, styling_rules, caplog):
    """Valid KPIs still render, and the dropped one is still surfaced."""
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        slide = render_slide(
            prs,
            layout_type="metric_summary",
            content={
                "title": "Key Metrics",
                "kpis": [{"name": "Revenue", "value": "1M"}, "oops", None],
            },
            brand=brand,
            styling_rules=styling_rules,
        )

    texts = _texts(slide)
    assert any("Revenue" in t for t in texts)
    assert any("1M" in t for t in texts)
    assert any("could not be displayed" in t for t in texts), (
        f"2 dropped KPIs left no marker on the slide; texts={texts!r}"
    )
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_non_list_kpis_value_is_also_reported(brand, styling_rules, caplog):
    """`{"kpis": None}` is discarded wholesale — that must warn too."""
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        render_slide(
            prs,
            layout_type="metric_summary",
            content={"title": "Key Metrics", "kpis": {"a": 1}},
            brand=brand,
            styling_rules=styling_rules,
        )
    assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a non-list `kpis` payload was discarded with no warning"
    )


def test_a_clean_deck_with_no_kpis_stays_clean(brand, styling_rules, caplog):
    """No marker and no warning when nothing was actually dropped."""
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        slide = render_slide(
            prs,
            layout_type="metric_summary",
            content={"title": "Key Metrics", "kpis": []},
            brand=brand,
            styling_rules=styling_rules,
        )
    texts = _texts(slide)
    assert not any("could not be displayed" in t for t in texts)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_null_title_and_trend_do_not_render_the_word_none(brand, styling_rules):
    """`str(None)` coercion must not paint the literal text "None" on a slide.

    Wrapping the caller value in bare ``str()`` stops the TypeError but swaps a
    crash for a cosmetic lie: a null `trend_description` becomes the truthy
    string "None" and gets rendered as italic body copy under the metrics.
    """
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="metric_summary",
        content={"title": None, "trend_description": None, "kpis": []},
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _texts(slide)
    assert "None" not in texts, f"literal 'None' painted onto the slide; texts={texts!r}"
