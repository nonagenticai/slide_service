from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer


class AgendaRenderer(BaseTemplateRenderer):
    def render(
        self,
        prs: Presentation,
        content: dict,
        brand: dict,
        styling_rules: dict,
    ) -> Slide:
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        # NOTE: agenda template currently has no decorative accent shape, so it
        # intentionally ignores the brand `secondary` colour. If a future design
        # pass adds an accent bar / coloured numbers / divider, restore the
        # `secondary` binding and apply it. See feature_spotlight + roadmap for
        # the canonical pattern.
        primary, _secondary = self._get_brand_colors(brand)
        font_family = self._get_font(brand)

        title_text = content.get("title", "Agenda")
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.8))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family
        p.alignment = PP_ALIGN.LEFT

        # Per-ELEMENT shapes are already guarded below (`isinstance(item, dict)`),
        # but the list itself was not: `{"items": null}` reached `enumerate()` and
        # raised TypeError, aborting the deck. Same class as metric_summary.kpis.
        items, dropped = self._collect_list_entries(
            content.get("items"), field="items", layout="agenda"
        )
        body_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.5), Inches(8.4), Inches(5))
        tf_body = body_box.text_frame
        tf_body.word_wrap = True

        for idx, item in enumerate(items):
            item_text = item.get("text", str(item)) if isinstance(item, dict) else str(item)

            p_item = tf_body.paragraphs[0] if idx == 0 else tf_body.add_paragraph()

            p_item.text = f"{idx + 1}.  {item_text}"
            p_item.font.size = Pt(18)
            p_item.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            p_item.font.name = font_family
            p_item.space_after = Pt(12)
            p_item.alignment = PP_ALIGN.LEFT

        # An agenda whose whole `items` payload was the wrong shape used to be
        # indistinguishable from a deliberately empty one: title, blank body,
        # no exception. Say so on the slide's own face.
        self._add_degraded_marker(
            slide,
            dropped=dropped,
            noun="agenda items",
            font_family=font_family,
            left=0.8,
            width=8.4,
            top=min(1.5 + len(items) * 0.5 + 0.1, 6.4),
        )

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
