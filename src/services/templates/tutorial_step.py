from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from src.services.image_layout import IMAGE_PLACEMENT_FULL_BLEED

from .base import BaseTemplateRenderer


class TutorialStepRenderer(BaseTemplateRenderer):
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
        image_placement, image_fit = self._resolve_asset_layout(
            content,
            styling_rules,
            "screenshot_url",
        )

        bg = slide.background
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFC)

        step_number = content.get("step_number", 1)
        badge = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.OVAL,
            Inches(0.8),
            Inches(0.82),
            Inches(0.65),
            Inches(0.65),
        )
        badge.fill.solid()
        badge.fill.fore_color.rgb = primary
        badge.line.fill.background()
        b_tf = badge.text_frame
        b_p = b_tf.paragraphs[0]
        b_p.text = str(step_number)
        b_p.font.size = Pt(20)
        b_p.font.bold = True
        b_p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        b_p.font.name = font_family
        b_p.alignment = PP_ALIGN.CENTER
        b_tf.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE

        title_text = content.get("title", "")
        if title_text:
            title_box = slide.shapes.add_textbox(
                Inches(1.65),
                Inches(0.82),
                Inches(slide_width - 2.45),
                Inches(0.7),
            )
            tf = title_box.text_frame
            tf.word_wrap = True
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]
            p.text = title_text
            p.font.size = Pt(24)
            p.font.bold = True
            p.font.color.rgb = primary
            p.font.name = font_family

        left_x = 0.8
        left_width = 4.35
        image_x = left_x + left_width + 0.55
        image_width = slide_width - image_x - 0.8
        image_left = image_x
        image_top = 1.85
        image_height = 3.95
        if image_placement == IMAGE_PLACEMENT_FULL_BLEED:
            image_left = image_x - 0.08
            image_top = 1.45
            image_width = slide_width - image_left
            image_height = slide_height - image_top - 0.95

        instruction = content.get("instruction", "")
        if instruction:
            body_panel = self._add_panel(
                slide,
                left=left_x,
                top=1.85,
                width=left_width,
                height=3.95,
                fill_color=RGBColor(0xFF, 0xFF, 0xFF),
                line_color=RGBColor(0xDA, 0xE2, 0xEA),
                rounded=True,
            )
            body_panel.shadow.inherit = False
            instr_box = slide.shapes.add_textbox(
                Inches(left_x + 0.2),
                Inches(2.05),
                Inches(left_width - 0.4),
                Inches(3.55),
            )
            i_tf = instr_box.text_frame
            i_tf.word_wrap = True
            i_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            i_p = i_tf.paragraphs[0]
            i_p.text = instruction
            i_p.font.size = Pt(14)
            i_p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            i_p.font.name = font_family

        screenshot_url = content.get("screenshot_url", "")
        self._add_treated_image(
            slide,
            image_ref=screenshot_url,
            left=image_left,
            top=image_top,
            width=image_width,
            height=image_height,
            font_family=font_family,
            placeholder_label="Screenshot preview unavailable",
            placement=image_placement,
            frame_fill_color=RGBColor(0xFF, 0xFF, 0xFF),
            frame_line_color=RGBColor(0xDA, 0xE2, 0xEA),
            fit_mode=image_fit,
        )

        tip = content.get("tip", "")
        if tip:
            tip_shape = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
                Inches(0.8),
                Inches(6.0),
                Inches(slide_width - 1.6),
                Inches(0.78),
            )
            tip_shape.fill.solid()
            tip_shape.fill.fore_color.rgb = RGBColor(0xE8, 0xF5, 0xE9)
            tip_shape.line.color.rgb = secondary

            t_tf = tip_shape.text_frame
            t_tf.word_wrap = True
            t_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            t_p = t_tf.paragraphs[0]
            t_p.text = f"Tip: {tip}"
            t_p.font.size = Pt(12)
            t_p.font.color.rgb = RGBColor(0x2E, 0x7D, 0x32)
            t_p.font.name = font_family

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
