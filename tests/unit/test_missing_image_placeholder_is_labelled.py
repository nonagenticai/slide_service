"""Every image placeholder must NAME what is missing.

``_add_treated_image`` degrades an unloadable ``image_ref`` to
``_add_image_placeholder``, which draws a grey panel captioned with
``placeholder_label``. ``feature_spotlight`` passed ``""`` — a silent blank grey
rectangle a reviewer cannot tell from a design choice — while every sibling call
site (comparison, finding_detail, tutorial_step) names it. In prod the common
cause is an expired presigned MinIO URL, so this is the frequent path, not the
exotic one.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.presentation import Presentation as PresentationType

from src.services.template_renderer import render_slide

_BRAND = {"primary_color": "#123456", "font_family": "Inter"}
_DEAD_REF = "http://[::1"  # unparseable => load_image_bytes returns None
_TEMPLATE_DIR = Path(inspect.getfile(render_slide)).parent / "templates"


def _slide_text(prs: PresentationType) -> str:
    return "\n".join(
        shape.text_frame.text for shape in prs.slides[0].shapes if shape.has_text_frame
    )


def test_feature_spotlight_labels_its_missing_image() -> None:
    prs = Presentation()
    render_slide(
        prs,
        layout_type="feature_spotlight",
        content={
            "title": "Realtime sync",
            "description": "Everything, everywhere",
            "image_url": _DEAD_REF,
        },
        brand=_BRAND,
        styling_rules={},
    )
    text = _slide_text(prs)
    assert "unavailable" in text.lower(), text
    assert "Feature image unavailable" in text, text


def test_no_template_passes_an_empty_placeholder_label() -> None:
    """Fleet-wide floor: a blank label is never a legitimate choice.

    ``feature_spotlight`` was the one site that did; this keeps a new template
    from reintroducing it without the author noticing.
    """
    offenders = [
        path.name
        for path in sorted(_TEMPLATE_DIR.glob("*.py"))
        if 'placeholder_label=""' in path.read_text()
    ]
    assert offenders == [], offenders


@pytest.mark.parametrize(
    ("layout_type", "content", "expected"),
    [
        (
            "finding_detail",
            {"title": "Slow LCP", "screenshot_url": _DEAD_REF},
            "Screenshot preview unavailable",
        ),
        (
            "tutorial_step",
            {"title": "Step 1", "screenshot_url": _DEAD_REF},
            "Screenshot preview unavailable",
        ),
    ],
)
def test_sibling_call_sites_stay_labelled(layout_type: str, content: dict, expected: str) -> None:
    """The siblings feature_spotlight was copied FROM — guards the premise."""
    prs = Presentation()
    render_slide(prs, layout_type=layout_type, content=content, brand=_BRAND, styling_rules={})
    assert expected in _slide_text(prs)
