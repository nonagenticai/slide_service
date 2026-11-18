"""Regression: no renderer may abort the deck over a malformed content list.

`content[<list field>]` is raw LLM output across every template. The object
wrapper gets forgotten (`["uptime", "latency"]`), a null lands where a list was
promised, or a dict arrives instead. Each renderer must survive all three and,
where entries are discarded, say so.

Covers the sibling renderers found in the same sweep as `metric_summary`.
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


# --------------------------------------------------------------------------
# scorecard - identical shape to metric_summary (the backlog's original address)
# --------------------------------------------------------------------------


def test_scorecard_survives_non_dict_metric_and_marks_it(brand, styling_rules, caplog):
    """`scorecard.py` L72-74 did bare `metric.get(...)` on LLM list elements."""
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        slide = render_slide(
            prs,
            layout_type="scorecard",
            content={
                "title": "Scores",
                "metrics": ["uptime", {"label": "Latency", "value": "12", "unit": "ms"}],
            },
            brand=brand,
            styling_rules=styling_rules,
        )
    texts = _texts(slide)
    assert any("Latency" in t for t in texts)
    assert any("12ms" in t for t in texts)
    assert any("could not be displayed" in t for t in texts)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_scorecard_fully_malformed_metrics_is_not_a_silent_empty_slide(
    brand, styling_rules, caplog
):
    prs = Presentation()
    with caplog.at_level(logging.WARNING):
        slide = render_slide(
            prs,
            layout_type="scorecard",
            content={"title": "Scores", "metrics": ["uptime", "latency"]},
            brand=brand,
            styling_rules=styling_rules,
        )
    texts = _texts(slide)
    assert any("Scores" in t for t in texts)
    assert any("could not be displayed" in t for t in texts), (
        f"fully-malformed scorecard rendered as a clean empty slide; texts={texts!r}"
    )


@pytest.mark.parametrize("payload", [None, {"a": 1}, "not-a-list", 7])
def test_scorecard_survives_a_non_list_metrics_payload(brand, styling_rules, payload):
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="scorecard",
        content={"title": "Scores", "metrics": payload},
        brand=brand,
        styling_rules=styling_rules,
    )
    assert any("Scores" in t for t in _texts(slide))


def test_scorecard_non_str_fields_do_not_paint_the_word_none(brand, styling_rules):
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="scorecard",
        content={
            "title": None,
            "footer": None,
            "metrics": [{"label": None, "value": 99, "unit": None}],
        },
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _texts(slide)
    assert any("99" in t for t in texts)
    assert not any(t == "None" for t in texts), f"literal 'None' on the slide; {texts!r}"


# --------------------------------------------------------------------------
# List-level-only siblings: elements were already isinstance-guarded, the LIST
# was not. These abort on `enumerate()` / a slice, upstream of any element check.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [None, {"a": 1}, 7])
def test_agenda_survives_a_non_list_items_payload(brand, styling_rules, payload):
    """agenda L47 `enumerate(items)` had no list guard -> TypeError on null."""
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="agenda",
        content={"title": "Agenda", "items": payload},
        brand=brand,
        styling_rules=styling_rules,
    )
    assert any("Agenda" in t for t in _texts(slide))


@pytest.mark.parametrize("payload", [None, {"a": 1}, 7])
def test_feature_spotlight_survives_a_non_list_bullets_payload(brand, styling_rules, payload):
    """`bullets[:max_bullets]` on a dict raised `unhashable type: 'slice'`."""
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="feature_spotlight",
        content={"title": "Feature", "bullets": payload},
        brand=brand,
        styling_rules=styling_rules,
    )
    assert any("Feature" in t for t in _texts(slide))


def test_section_divider_survives_a_non_str_entry_field(brand, styling_rules):
    """`entry.get("text")` returned the RAW value, then `.strip()` -> AttributeError."""
    prs = Presentation()
    slide = render_slide(
        prs,
        layout_type="section_divider",
        content={"title": "Part Two", "items": [{"text": 3}, {"label": None}, "ok"]},
        brand=brand,
        styling_rules=styling_rules,
    )
    texts = _texts(slide)
    assert any("Part Two" in t for t in texts)
    assert any("3" in t for t in texts)
