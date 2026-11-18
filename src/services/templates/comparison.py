from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from src.services.image_layout import IMAGE_PLACEMENT_FULL_BLEED

from .base import BaseTemplateRenderer


class ComparisonRenderer(BaseTemplateRenderer):
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
        before_placement, before_fit = self._resolve_asset_layout(
            content,
            styling_rules,
            "before_image_url",
        )
        after_placement, after_fit = self._resolve_asset_layout(
            content,
            styling_rules,
            "after_image_url",
        )
        image_placement = (
            IMAGE_PLACEMENT_FULL_BLEED
            if IMAGE_PLACEMENT_FULL_BLEED in {before_placement, after_placement}
            else before_placement
        )
        caption = content.get("caption", "")

        bg = slide.background
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFC)

        title_text = content.get("title", "Comparison")
        title_box = slide.shapes.add_textbox(
            Inches(0.8),
            Inches(0.85),
            Inches(slide_width - 1.6),
            Inches(0.8),
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(25)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family
        p.alignment = PP_ALIGN.CENTER

        before_label = content.get("before_label", "Before")
        after_label = content.get("after_label", "After")
        margin = 0.8
        gap = 0.45
        card_top = 1.95
        bottom_margin = 1.1
        header_top = 1.55
        if image_placement == IMAGE_PLACEMENT_FULL_BLEED:
            margin = 0.0
            gap = 0.08
            card_top = 1.4
            bottom_margin = 0.96 if caption else 0.7
            header_top = 1.02
        card_height = slide_height - card_top - bottom_margin
        card_width = (slide_width - margin * 2 - gap) / 2
        left_x = margin
        right_x = left_x + card_width + gap

        left_header = slide.shapes.add_textbox(
            Inches(left_x + 0.1),
            Inches(header_top),
            Inches(card_width - 0.2),
            Inches(0.4),
        )
        lh_tf = left_header.text_frame
        lh_p = lh_tf.paragraphs[0]
        lh_p.text = before_label
        lh_p.font.size = Pt(17)
        lh_p.font.bold = True
        lh_p.font.color.rgb = primary
        lh_p.font.name = font_family
        lh_p.alignment = PP_ALIGN.CENTER

        right_header = slide.shapes.add_textbox(
            Inches(right_x + 0.1),
            Inches(header_top),
            Inches(card_width - 0.2),
            Inches(0.4),
        )
        rh_tf = right_header.text_frame
        rh_p = rh_tf.paragraphs[0]
        rh_p.text = after_label
        rh_p.font.size = Pt(17)
        rh_p.font.bold = True
        rh_p.font.color.rgb = secondary
        rh_p.font.name = font_family
        rh_p.alignment = PP_ALIGN.CENTER

        before_url = content.get("before_image_url", "")
        self._add_treated_image(
            slide,
            image_ref=before_url,
            left=left_x,
            top=card_top,
            width=card_width,
            height=card_height,
            font_family=font_family,
            placeholder_label="Before state unavailable",
            placement=before_placement,
            frame_fill_color=RGBColor(0xFF, 0xFF, 0xFF),
            frame_line_color=RGBColor(0xDA, 0xE2, 0xEA),
            fit_mode=before_fit,
        )

        after_url = content.get("after_image_url", "")
        self._add_treated_image(
            slide,
            image_ref=after_url,
            left=right_x,
            top=card_top,
            width=card_width,
            height=card_height,
            font_family=font_family,
            placeholder_label="After state unavailable",
            placement=after_placement,
            frame_fill_color=RGBColor(0xFF, 0xFF, 0xFF),
            frame_line_color=RGBColor(0xDA, 0xE2, 0xEA),
            fit_mode=after_fit,
        )

        split_rule = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(left_x + card_width + gap / 2 - 0.03),
            Inches(card_top + 0.35),
            Inches(0.06),
            Inches(card_height - 0.7),
        )
        split_rule.fill.solid()
        split_rule.fill.fore_color.rgb = RGBColor(0xE5, 0xEB, 0xF2)
        split_rule.line.fill.background()

        if caption:
            cap_box = slide.shapes.add_textbox(
                Inches(1.0),
                Inches(6.65),
                Inches(slide_width - 2.0),
                Inches(0.45),
            )
            cap_tf = cap_box.text_frame
            cap_tf.word_wrap = True
            cap_p = cap_tf.paragraphs[0]
            cap_p.text = caption
            cap_p.font.size = Pt(11)
            cap_p.font.italic = True
            cap_p.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            cap_p.font.name = font_family
            cap_p.alignment = PP_ALIGN.CENTER

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
