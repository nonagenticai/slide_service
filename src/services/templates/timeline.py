from __future__ import annotations

import logging

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer

logger = logging.getLogger(__name__)

_MAX_BEATS = 6


class TimelineRenderer(BaseTemplateRenderer):
    def render(
        self,
        prs: Presentation,
        content: dict,
        brand: dict,
        styling_rules: dict,
    ) -> Slide:
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        primary, secondary = self._get_brand_colors(brand)
        font_family = self._get_font(brand)

        title_text = content.get("title") or "Timeline"
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.7))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family

        beats, dropped = self._normalise_beats(content)
        if not beats:
            # The early return is the silent path: a payload whose every beat was
            # unrenderable produced a title and a blank canvas, with nothing to
            # tell it apart from a timeline that was legitimately empty.
            self._add_degraded_marker(
                slide,
                dropped=dropped,
                noun="timeline beats",
                font_family=font_family,
                top=1.8,
            )
            self._add_speaker_notes(slide, content.get("notes", ""))
            return slide

        num = len(beats)
        lane_width = 8.5 / num
        rail_y = 3.2

        rail = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0.75),
            Inches(rail_y),
            Inches(8.5),
            Inches(0.08),
        )
        rail.fill.solid()
        rail.fill.fore_color.rgb = secondary
        rail.line.fill.background()

        for idx, beat in enumerate(beats):
            x = 0.75 + idx * lane_width
            center_x = x + lane_width / 2

            badge = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.OVAL,
                Inches(center_x - 0.32),
                Inches(rail_y - 0.28),
                Inches(0.64),
                Inches(0.64),
            )
            badge.fill.solid()
            badge.fill.fore_color.rgb = primary
            badge.line.fill.background()
            b_tf = badge.text_frame
            b_tf.word_wrap = True
            b_p = b_tf.paragraphs[0]
            b_p.text = beat["marker"]
            b_p.alignment = PP_ALIGN.CENTER
            b_p.font.size = Pt(12)
            b_p.font.bold = True
            b_p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            b_p.font.name = font_family

            label_box = slide.shapes.add_textbox(
                Inches(x),
                Inches(1.4),
                Inches(lane_width - 0.1),
                Inches(1.5),
            )
            l_tf = label_box.text_frame
            l_tf.word_wrap = True
            l_p = l_tf.paragraphs[0]
            l_p.text = beat["label"]
            l_p.alignment = PP_ALIGN.CENTER
            l_p.font.size = Pt(14)
            l_p.font.bold = True
            l_p.font.color.rgb = primary
            l_p.font.name = font_family

            if beat["description"]:
                desc_box = slide.shapes.add_textbox(
                    Inches(x),
                    Inches(4.0),
                    Inches(lane_width - 0.1),
                    Inches(2.5),
                )
                d_tf = desc_box.text_frame
                d_tf.word_wrap = True
                d_p = d_tf.paragraphs[0]
                d_p.text = beat["description"]
                d_p.alignment = PP_ALIGN.CENTER
                d_p.font.size = Pt(10)
                d_p.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                d_p.font.name = font_family

        # A timeline that lost SOME beats still renders a plausible rail; the
        # marker is what stops "two beats were destroyed" from reading as
        # "there were only three".
        self._add_degraded_marker(
            slide,
            dropped=dropped,
            noun="timeline beats",
            font_family=font_family,
            top=6.4,
        )

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide

    def _normalise_beats(self, content: dict) -> tuple[list[dict[str, str]], int]:
        """Reduce the sequential slot to beats. Returns ``(beats, dropped)``.

        ``dropped`` counts a non-list payload as one loss and every entry that
        normalised to no label AND no description — the entries that used to
        vanish through the bare ``continue`` below without a warning, a count, or
        anything on the slide. Over-limit truncation is deliberately NOT counted
        (a timeline that shows 6 of 8 beats did what it was designed to do),
        matching ``html_slide_renderer._beats``.
        """
        raw, dropped = self._collect_list_entries(
            content.get("beats")
            or content.get("phases")
            or content.get("steps")
            or content.get("bullets"),
            field="beats",
            layout="timeline",
        )
        beats: list[dict[str, str]] = []
        for idx, item in enumerate(raw):
            if len(beats) >= _MAX_BEATS:
                break
            if isinstance(item, dict):
                marker = (
                    item.get("marker")
                    or item.get("timeline")
                    or item.get("number")
                    or item.get("step_number")
                    or f"{idx + 1:02d}"
                )
                label = item.get("label") or item.get("name") or item.get("title")
                description = item.get("description") or item.get("text") or item.get("detail")
            else:
                marker = f"{idx + 1:02d}"
                label = str(item)
                description = None

            if not label and not description:
                dropped += 1
                continue

            beats.append(
                {
                    "marker": str(marker),
                    "label": str(label) if label else "",
                    "description": str(description) if description else "",
                }
            )
        if dropped:
            logger.warning(
                "timeline: dropped %d unrenderable beat entries; rendering the %d valid one(s)",
                dropped,
                len(beats),
            )
        return beats, dropped
