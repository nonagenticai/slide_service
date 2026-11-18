from __future__ import annotations

import logging
from typing import Any

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer

logger = logging.getLogger(__name__)


def _normalise_bullets(raw: Any, *, side: str) -> tuple[list[str], int]:
    """Reduce one column's bullets to non-empty strings. Returns ``(bullets, dropped)``.

    ``dropped`` counts a non-list payload as one loss and every entry that
    normalised to empty text — a null, an empty string, or a dict with neither
    ``text`` nor ``label``. Those used to disappear with no warning and no
    count, so a column whose bullets were all destroyed rendered as a heading
    over clean white space. Over-limit truncation is NOT counted, matching
    ``html_slide_renderer._str_list``.
    """
    if raw is None:
        return [], 0
    if not isinstance(raw, list | tuple):
        logger.warning(
            "two_up: discarding non-list %s_bullets payload of type %s; expected a list",
            side,
            type(raw).__name__,
        )
        return [], 1
    bullets: list[str] = []
    dropped = 0
    for item in raw:
        if len(bullets) >= 6:
            break
        raw_text = item.get("text") or item.get("label") or "" if isinstance(item, dict) else item
        text = "" if raw_text is None else str(raw_text).strip()
        if text:
            bullets.append(text)
        else:
            dropped += 1
    if dropped:
        logger.warning(
            "two_up: dropped %d unrenderable %s_bullets entries; rendering the %d valid one(s)",
            dropped,
            side,
            len(bullets),
        )
    return bullets, dropped


def _read_side(content: dict, side: str) -> tuple[str, str, list[str], int]:
    value = content.get(side)
    if isinstance(value, dict):
        heading = value.get("heading") or ""
        body = value.get("body") or ""
        bullets, dropped = _normalise_bullets(value.get("bullets"), side=side)
    else:
        heading = content.get(f"{side}_heading") or ""
        body = content.get(f"{side}_body") or ""
        bullets, dropped = _normalise_bullets(content.get(f"{side}_bullets"), side=side)
    return str(heading).strip(), str(body).strip(), bullets, dropped


class TwoUpRenderer(BaseTemplateRenderer):
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
        slide_width, slide_height = self._slide_size_inches(prs)

        bg = slide.background
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFC)

        title_text = content.get("title") or ""
        title_box = slide.shapes.add_textbox(
            Inches(0.8),
            Inches(0.6),
            Inches(slide_width - 1.6),
            Inches(0.9),
        )
        t_tf = title_box.text_frame
        t_tf.word_wrap = True
        t_tf.auto_size = MSO_AUTO_SIZE.NONE
        t_p = t_tf.paragraphs[0]
        t_p.text = title_text
        t_p.font.size = Pt(25)
        t_p.font.bold = True
        t_p.font.color.rgb = primary
        t_p.font.name = font_family
        t_p.alignment = PP_ALIGN.CENTER

        margin = 0.8
        gap = 0.5
        card_top = 1.75
        bottom_margin = 0.7
        card_height = slide_height - card_top - bottom_margin
        card_width = (slide_width - margin * 2 - gap) / 2
        left_x = margin
        right_x = left_x + card_width + gap

        accents = (primary, secondary)
        for index, side in enumerate(("left", "right")):
            panel_x = left_x if side == "left" else right_x
            accent = accents[index]
            heading, body, bullets, dropped = _read_side(content, side)

            self._add_panel(
                slide,
                left=panel_x,
                top=card_top,
                width=card_width,
                height=card_height,
                fill_color=RGBColor(0xFF, 0xFF, 0xFF),
                line_color=RGBColor(0xDA, 0xE2, 0xEA),
                rounded=True,
            )

            text_box = slide.shapes.add_textbox(
                Inches(panel_x + 0.3),
                Inches(card_top + 0.3),
                Inches(card_width - 0.6),
                Inches(card_height - 0.6),
            )
            tf = text_box.text_frame
            tf.word_wrap = True
            tf.auto_size = MSO_AUTO_SIZE.NONE
            first = True

            if heading:
                p = tf.paragraphs[0]
                p.text = heading
                p.font.size = Pt(18)
                p.font.bold = True
                p.font.color.rgb = accent
                p.font.name = font_family
                first = False

            if body:
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                p.text = body
                p.font.size = Pt(13)
                p.font.color.rgb = RGBColor(0x33, 0x3B, 0x44)
                p.font.name = font_family
                p.space_after = Pt(6)
                first = False

            for bullet in bullets:
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                p.text = f"• {bullet}"
                p.font.size = Pt(13)
                p.font.color.rgb = RGBColor(0x33, 0x3B, 0x44)
                p.font.name = font_family
                p.space_after = Pt(3)
                first = False

            # Per-COLUMN, not per-slide: a two_up whose right column was
            # destroyed and whose left one is intact must say WHICH side is
            # thin, otherwise the marker sends a reviewer to the wrong half.
            self._add_degraded_marker(
                slide,
                dropped=dropped,
                noun=f"{side} bullets",
                font_family=font_family,
                left=panel_x + 0.25,
                width=card_width - 0.5,
                top=card_top + card_height - 0.55,
            )

        divider = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(left_x + card_width + gap / 2 - 0.03),
            Inches(card_top + 0.35),
            Inches(0.06),
            Inches(card_height - 0.7),
        )
        divider.fill.solid()
        divider.fill.fore_color.rgb = RGBColor(0xE5, 0xEB, 0xF2)
        divider.line.fill.background()

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
