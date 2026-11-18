from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer, as_text

_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_NEAR_WHITE = RGBColor(0xE8, 0xEA, 0xED)


class SectionDividerRenderer(BaseTemplateRenderer):
    """Full-bleed chapter break / section divider (also used for agenda).

    Brand-primary background, small accent-colored uppercase eyebrow, a large
    bold white title, and an optional light numbered list beneath.
    """

    @staticmethod
    def _normalise_items(content: dict) -> list[str]:
        raw = content.get("items") or content.get("bullets") or []
        if not isinstance(raw, list):
            raw = []
        items: list[str] = []
        for entry in raw:
            if isinstance(entry, dict):
                text = entry.get("text") or entry.get("label") or entry.get("name") or ""
            else:
                text = entry
            # `str(...)` before `.strip()`: the dict branch returns the RAW value,
            # so `{"text": 3}` used to raise AttributeError on `int.strip`.
            # `two_up._normalise_bullets` already coerces this way.
            text = as_text(text).strip()
            if text:
                items.append(text)
        return items

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
        slide_w, slide_h = self._slide_size_inches(prs)

        # -- Full-bleed brand-primary background (unconditional) --------------
        hero = self._add_panel(
            slide,
            left=0.0,
            top=0.0,
            width=slide_w,
            height=slide_h,
            fill_color=primary,
            rounded=False,
        )
        hero.line.fill.background()

        eyebrow_text = (
            content.get("eyebrow") or content.get("chapter") or content.get("kicker") or ""
        )
        title_text = content.get("title") or ""
        items = self._normalise_items(content)

        margin_x = 1.2
        text_width = slide_w - margin_x * 2

        # -- Eyebrow ----------------------------------------------------------
        if eyebrow_text:
            eyebrow_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(0.9),
                Inches(text_width),
                Inches(0.5),
            )
            tf_eb = eyebrow_box.text_frame
            tf_eb.word_wrap = True
            p_eb = tf_eb.paragraphs[0]
            p_eb.text = eyebrow_text.upper()
            p_eb.font.size = Pt(16)
            p_eb.font.bold = True
            p_eb.font.color.rgb = secondary
            p_eb.font.name = font_family
            p_eb.alignment = PP_ALIGN.LEFT

        # -- Title ------------------------------------------------------------
        title_font_size = 44 if len(title_text) <= 60 else (36 if len(title_text) <= 100 else 30)
        title_box = slide.shapes.add_textbox(
            Inches(margin_x),
            Inches(1.7),
            Inches(text_width),
            Inches(2.4),
        )
        tf_t = title_box.text_frame
        tf_t.word_wrap = True
        p_t = tf_t.paragraphs[0]
        p_t.text = title_text
        p_t.font.size = Pt(title_font_size)
        p_t.font.bold = True
        p_t.font.color.rgb = _WHITE
        p_t.font.name = font_family
        p_t.alignment = PP_ALIGN.LEFT

        # -- Numbered list ----------------------------------------------------
        if items:
            list_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(4.2),
                Inches(text_width),
                Inches(slide_h - 4.6),
            )
            tf_l = list_box.text_frame
            tf_l.word_wrap = True
            for idx, item_text in enumerate(items):
                p_i = tf_l.paragraphs[0] if idx == 0 else tf_l.add_paragraph()
                p_i.text = f"{idx + 1}.  {item_text}"
                p_i.font.size = Pt(18)
                p_i.font.color.rgb = _NEAR_WHITE
                p_i.font.name = font_family
                p_i.space_after = Pt(10)
                p_i.alignment = PP_ALIGN.LEFT

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
