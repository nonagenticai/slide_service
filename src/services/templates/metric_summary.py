from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer, as_text, hex_to_rgb


class MetricSummaryRenderer(BaseTemplateRenderer):
    def render(
        self,
        prs: Presentation,
        content: dict,
        brand: dict,
        styling_rules: dict,
    ) -> Slide:
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        # NOTE: KPI cards currently use hardcoded neutral colours (light off-
        # white fill + gray border) so the brand `secondary` accent is not
        # applied. If a design pass colours the card borders or adds a header
        # rule, restore the `secondary` binding and apply it. See scorecard
        # `header_rule` for the canonical pattern.
        primary, _secondary = self._get_brand_colors(brand)
        font_family = self._get_font(brand)

        title_text = as_text(content.get("title"), "Key Metrics")
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.7))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family

        # `content["kpis"]` is raw LLM output. Both the list itself and each
        # element may be the wrong shape; `_collect_dict_items` filters to the
        # usable dicts, WARNS for everything it discards, and never raises.
        # Filtering up front (rather than `continue`-ing inside the loop) also
        # keeps the card grid dense -- a skipped entry no longer leaves a hole.
        kpis, dropped = self._collect_dict_items(
            content.get("kpis"), field="kpis", layout="metric_summary"
        )
        max_cols = min(len(kpis), 3) if kpis else 1
        card_w = 8.0 / max_cols
        start_x = 1.0

        for idx, kpi in enumerate(kpis):
            col = idx % max_cols
            row = idx // max_cols
            x = start_x + col * card_w
            y = 1.5 + row * 2.4

            # Every field here reaches a `paragraph.text` sink, which rejects
            # non-str. `as_text` coerces without turning a null into the literal
            # word "None" on the finished slide.
            name = as_text(kpi.get("name"))
            value = as_text(kpi.get("value"))
            delta = as_text(kpi.get("delta"))
            unit = as_text(kpi.get("unit"))

            card = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
                Inches(x),
                Inches(y),
                Inches(card_w - 0.3),
                Inches(2.0),
            )
            card.fill.solid()
            card.fill.fore_color.rgb = RGBColor(0xF8, 0xF9, 0xFA)
            card.line.color.rgb = RGBColor(0xE0, 0xE0, 0xE0)

            card_tf = card.text_frame
            card_tf.word_wrap = True

            p_name = card_tf.paragraphs[0]
            p_name.text = name
            p_name.font.size = Pt(12)
            p_name.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            p_name.font.name = font_family
            p_name.alignment = PP_ALIGN.CENTER
            p_name.space_after = Pt(4)

            p_value = card_tf.add_paragraph()
            display = f"{value}{unit}" if unit else value
            p_value.text = display
            p_value.font.size = Pt(32)
            p_value.font.bold = True
            p_value.font.color.rgb = primary
            p_value.font.name = font_family
            p_value.alignment = PP_ALIGN.CENTER

            if delta:
                p_delta = card_tf.add_paragraph()
                p_delta.text = delta
                p_delta.font.size = Pt(12)
                p_delta.font.name = font_family
                p_delta.alignment = PP_ALIGN.CENTER
                delta_str = delta
                if delta_str.startswith("+") or delta_str.startswith("\u2191"):
                    p_delta.font.color.rgb = hex_to_rgb("#34a853")
                elif delta_str.startswith("-") or delta_str.startswith("\u2193"):
                    p_delta.font.color.rgb = hex_to_rgb("#d93025")
                else:
                    p_delta.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        # A slide that lost KPIs says so on its own face. Sits just under the
        # last card row so it reads as part of the metric panel, above `trend`.
        rows = -(-len(kpis) // max_cols) if kpis else 0
        self._add_degraded_marker(
            slide,
            dropped=dropped,
            noun="metrics",
            font_family=font_family,
            top=min(1.5 + rows * 2.4 + 0.1, 5.4),
        )

        trend = as_text(content.get("trend_description"))
        if trend:
            trend_box = slide.shapes.add_textbox(Inches(0.5), Inches(6.0), Inches(9), Inches(0.7))
            t_tf = trend_box.text_frame
            t_tf.word_wrap = True
            t_p = t_tf.paragraphs[0]
            t_p.text = trend
            t_p.font.size = Pt(12)
            t_p.font.italic = True
            t_p.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            t_p.font.name = font_family
            t_p.alignment = PP_ALIGN.CENTER

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
