from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer, as_text


class ScorecardRenderer(BaseTemplateRenderer):
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
        slide_width, _ = self._slide_size_inches(prs)

        bg = slide.background
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFC)

        header_rule = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0.7),
            Inches(0.72),
            Inches(slide_width - 1.4),
            Inches(0.08),
        )
        header_rule.fill.solid()
        header_rule.fill.fore_color.rgb = secondary
        header_rule.line.fill.background()

        title_text = as_text(content.get("title"), "Scorecard")
        title_box = slide.shapes.add_textbox(
            Inches(0.8),
            Inches(0.95),
            Inches(slide_width - 1.6),
            Inches(0.7),
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(26)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family

        # Same defect class as `metric_summary.kpis`: `content["metrics"]` is
        # raw LLM output, so the list itself and each element may be the wrong
        # shape. `["uptime", "latency"]` (object wrapper forgotten) used to hit
        # `metric.get(...)` -> AttributeError and abort the entire deck build.
        # Discards are WARNED and marked on the slide, never silent.
        metrics, dropped = self._collect_dict_items(
            content.get("metrics"), field="metrics", layout="scorecard"
        )
        max_cols = min(len(metrics), 4) if metrics else 1
        content_width = slide_width - 1.6
        gap = 0.28
        total_gap = gap * (max_cols - 1)
        col_width = (content_width - total_gap) / max_cols
        start_x = 0.8

        for idx, metric in enumerate(metrics):
            col = idx % max_cols
            row = idx // max_cols
            x = start_x + col * (col_width + gap)
            y = 2.0 + row * 2.18

            label = as_text(metric.get("label"))
            value = as_text(metric.get("value"))
            unit = as_text(metric.get("unit"))

            panel = self._add_panel(
                slide,
                left=x,
                top=y,
                width=col_width,
                height=1.75,
                fill_color=RGBColor(0xFF, 0xFF, 0xFF),
                line_color=RGBColor(0xDA, 0xE2, 0xEA),
                rounded=True,
            )
            panel.shadow.inherit = False

            card = slide.shapes.add_textbox(
                Inches(x + 0.12),
                Inches(y + 0.2),
                Inches(col_width - 0.24),
                Inches(1.32),
            )
            tf_card = card.text_frame
            tf_card.word_wrap = True

            p_val = tf_card.paragraphs[0]
            p_val.text = f"{value}{unit}"
            p_val.font.size = Pt(30)
            p_val.font.bold = True
            p_val.font.color.rgb = primary
            p_val.font.name = font_family
            p_val.alignment = PP_ALIGN.CENTER

            p_label = tf_card.add_paragraph()
            p_label.text = label
            p_label.font.size = Pt(14)
            p_label.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            p_label.font.name = font_family
            p_label.alignment = PP_ALIGN.CENTER

        rows = -(-len(metrics) // max_cols) if metrics else 0
        self._add_degraded_marker(
            slide,
            dropped=dropped,
            noun="metrics",
            font_family=font_family,
            top=min(2.0 + rows * 2.18 + 0.08, 6.1),
            left=0.8,
            width=slide_width - 1.6,
        )

        footer_text = as_text(content.get("footer"))
        if footer_text:
            footer_box = slide.shapes.add_textbox(
                Inches(0.8),
                Inches(6.55),
                Inches(slide_width - 1.6),
                Inches(0.4),
            )
            tf_footer = footer_box.text_frame
            p_footer = tf_footer.paragraphs[0]
            p_footer.text = footer_text
            p_footer.font.size = Pt(10)
            p_footer.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
            p_footer.font.name = font_family
            p_footer.alignment = PP_ALIGN.CENTER

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
