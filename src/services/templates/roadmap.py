from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer


class RoadmapRenderer(BaseTemplateRenderer):
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

        title_text = content.get("title", "Roadmap")
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.7))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family

        # Same class as process_flow.steps: a non-list `phases` used to be
        # iterated per character / per dict key into a plausible-looking roadmap.
        phases, dropped = self._collect_list_entries(
            content.get("phases"), field="phases", layout="roadmap"
        )
        if not phases:
            self._add_degraded_marker(
                slide,
                dropped=dropped,
                noun="roadmap phases",
                font_family=font_family,
                top=1.8,
            )
            self._add_speaker_notes(slide, content.get("notes", ""))
            return slide

        num_phases = len(phases)
        lane_width = 8.5 / num_phases
        timeline_y = 3.2

        timeline = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0.75),
            Inches(timeline_y),
            Inches(8.5),
            Inches(0.08),
        )
        timeline.fill.solid()
        # Timeline bar is decorative — use `secondary` so it differentiates
        # from primary-coloured phase names below. Was `primary` — visually
        # blended into the title row + caused `secondary` to be RUF059-unused.
        timeline.fill.fore_color.rgb = secondary
        timeline.line.fill.background()

        for idx, phase in enumerate(phases):
            phase_name = (
                phase.get("name", f"Phase {idx + 1}") if isinstance(phase, dict) else str(phase)
            )
            phase_items = phase.get("items", []) if isinstance(phase, dict) else []
            phase_date = phase.get("date", "") if isinstance(phase, dict) else ""

            x = 0.75 + idx * lane_width

            dot = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.OVAL,
                Inches(x + lane_width / 2 - 0.15),
                Inches(timeline_y - 0.11),
                Inches(0.3),
                Inches(0.3),
            )
            dot.fill.solid()
            dot.fill.fore_color.rgb = primary
            dot.line.fill.background()

            name_box = slide.shapes.add_textbox(
                Inches(x),
                Inches(1.5),
                Inches(lane_width - 0.1),
                Inches(0.5),
            )
            n_tf = name_box.text_frame
            n_tf.word_wrap = True
            n_p = n_tf.paragraphs[0]
            n_p.text = phase_name
            n_p.font.size = Pt(14)
            n_p.font.bold = True
            n_p.font.color.rgb = primary
            n_p.font.name = font_family
            n_p.alignment = PP_ALIGN.CENTER

            if phase_date:
                date_box = slide.shapes.add_textbox(
                    Inches(x),
                    Inches(2.0),
                    Inches(lane_width - 0.1),
                    Inches(0.4),
                )
                d_tf = date_box.text_frame
                d_p = d_tf.paragraphs[0]
                d_p.text = phase_date
                d_p.font.size = Pt(10)
                d_p.font.italic = True
                d_p.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
                d_p.font.name = font_family
                d_p.alignment = PP_ALIGN.CENTER

            if phase_items:
                items_box = slide.shapes.add_textbox(
                    Inches(x),
                    Inches(3.8),
                    Inches(lane_width - 0.1),
                    Inches(2.5),
                )
                i_tf = items_box.text_frame
                i_tf.word_wrap = True

                for i_idx, item in enumerate(phase_items):
                    item_text = item.get("text", str(item)) if isinstance(item, dict) else str(item)
                    ip = i_tf.paragraphs[0] if i_idx == 0 else i_tf.add_paragraph()
                    ip.text = f"\u2022 {item_text}"
                    ip.font.size = Pt(10)
                    ip.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                    ip.font.name = font_family
                    ip.space_after = Pt(4)

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
