"""Dispatches slide rendering to per-template Python renderers."""

from __future__ import annotations

import logging
from typing import Any

from pptx.slide import Slide

from src.services.templates.agenda import AgendaRenderer
from src.services.templates.base import BaseTemplateRenderer
from src.services.templates.comparison import ComparisonRenderer
from src.services.templates.cta import CTARenderer
from src.services.templates.feature_spotlight import FeatureSpotlightRenderer
from src.services.templates.finding_detail import FindingDetailRenderer
from src.services.templates.metric_summary import MetricSummaryRenderer
from src.services.templates.process_flow import ProcessFlowRenderer
from src.services.templates.quote import QuoteRenderer
from src.services.templates.roadmap import RoadmapRenderer
from src.services.templates.scorecard import ScorecardRenderer
from src.services.templates.section_divider import SectionDividerRenderer
from src.services.templates.stat import StatRenderer
from src.services.templates.timeline import TimelineRenderer
from src.services.templates.title import TitleRenderer
from src.services.templates.tutorial_step import TutorialStepRenderer
from src.services.templates.two_up import TwoUpRenderer

logger = logging.getLogger(__name__)

_RENDERERS: dict[str, BaseTemplateRenderer] = {
    "title": TitleRenderer(),
    "agenda": AgendaRenderer(),
    "scorecard": ScorecardRenderer(),
    "finding_detail": FindingDetailRenderer(),
    "comparison": ComparisonRenderer(),
    "feature_spotlight": FeatureSpotlightRenderer(),
    "process_flow": ProcessFlowRenderer(),
    "metric_summary": MetricSummaryRenderer(),
    "roadmap": RoadmapRenderer(),
    "cta": CTARenderer(),
    "tutorial_step": TutorialStepRenderer(),
    # SLIDE-2 layout families (PPTX twins of the HTML/token layouts)
    "quote": QuoteRenderer(),
    "stat": StatRenderer(),
    "section_divider": SectionDividerRenderer(),
    "timeline": TimelineRenderer(),
    "two_up": TwoUpRenderer(),
    # Aliases — must mirror html_slide_renderer._FAMILY_BY_LAYOUT so both
    # renderers accept the SAME layout vocabulary. deck_builder renders the PPTX
    # FIRST and hard-raises ValueError on an unknown key, which fails the WHOLE
    # deck build even when the HTML preview rendered fine. Keep these in lockstep
    # with the HTML aliases (testimonial->quote, metric_hero->stat, split->two_up).
    "testimonial": QuoteRenderer(),
    "metric_hero": StatRenderer(),
    "split": TwoUpRenderer(),
}


def render_slide(
    prs: Any,
    layout_type: str,
    content: dict,
    brand: dict,
    styling_rules: dict,
) -> Slide:
    """Dispatch rendering to the appropriate template renderer."""
    renderer = _RENDERERS.get(layout_type)
    if renderer is None:
        raise ValueError(f"Unknown layout_type: {layout_type}")
    return renderer.render(prs, content, brand, styling_rules)
