from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer


class ProcessFlowRenderer(BaseTemplateRenderer):
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

        title_text = content.get("title", "Process Flow")
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.7))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family

        # `content["steps"]` is raw LLM output: a bare string used to be iterated
        # per character into one step per letter, and a dict into one step per
        # KEY. Both rendered as a confident, successful diagram of nonsense.
        steps, dropped = self._collect_list_entries(
            content.get("steps"), field="steps", layout="process_flow"
        )
        if not steps:
            # The early return is the silent path: no steps, no marker, a slide
            # that is just a title. Mark it BEFORE returning.
            self._add_degraded_marker(
                slide,
                dropped=dropped,
                noun="process steps",
                font_family=font_family,
                top=1.8,
            )
            self._add_speaker_notes(slide, content.get("notes", ""))
            return slide

        max_per_row = min(len(steps), 5)
        step_width = 8.0 / max_per_row
        arrow_margin = 0.15

        for idx, step in enumerate(steps):
            col = idx % max_per_row
            row = idx // max_per_row
            x = 1.0 + col * step_width
            y = 1.6 + row * 2.5

            step_label = step.get("label", str(step)) if isinstance(step, dict) else str(step)
            step_desc = step.get("description", "") if isinstance(step, dict) else ""

            circle = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.OVAL,
                Inches(x + step_width / 2 - 0.3),
                Inches(y),
                Inches(0.6),
                Inches(0.6),
            )
            circle.fill.solid()
            circle.fill.fore_color.rgb = primary
            circle.line.fill.background()
            c_tf = circle.text_frame
            c_p = c_tf.paragraphs[0]
            c_p.text = str(idx + 1)
            c_p.font.size = Pt(14)
            c_p.font.bold = True
            c_p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            c_p.font.name = font_family
            c_p.alignment = PP_ALIGN.CENTER

            label_box = slide.shapes.add_textbox(
                Inches(x),
                Inches(y + 0.75),
                Inches(step_width - arrow_margin),
                Inches(0.4),
            )
            l_tf = label_box.text_frame
            l_tf.word_wrap = True
            l_p = l_tf.paragraphs[0]
            l_p.text = step_label
            l_p.font.size = Pt(12)
            l_p.font.bold = True
            l_p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            l_p.font.name = font_family
            l_p.alignment = PP_ALIGN.CENTER

            if step_desc:
                desc_box = slide.shapes.add_textbox(
                    Inches(x),
                    Inches(y + 1.15),
                    Inches(step_width - arrow_margin),
                    Inches(0.6),
                )
                d_tf = desc_box.text_frame
                d_tf.word_wrap = True
                d_p = d_tf.paragraphs[0]
                d_p.text = step_desc
                d_p.font.size = Pt(10)
                d_p.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
                d_p.font.name = font_family
                d_p.alignment = PP_ALIGN.CENTER

            if col < max_per_row - 1 and idx < len(steps) - 1:
                arrow = slide.shapes.add_shape(
                    MSO_AUTO_SHAPE_TYPE.RIGHT_ARROW,
                    Inches(x + step_width - 0.35),
                    Inches(y + 0.15),
                    Inches(0.3),
                    Inches(0.3),
                )
                arrow.fill.solid()
                arrow.fill.fore_color.rgb = secondary
                arrow.line.fill.background()

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
