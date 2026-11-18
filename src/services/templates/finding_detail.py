from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from src.services.image_layout import IMAGE_PLACEMENT_FULL_BLEED

from .base import BaseTemplateRenderer, hex_to_rgb

_SEVERITY_COLORS: dict[str, str] = {
    "critical": "#d93025",
    "high": "#ea4335",
    "medium": "#fbbc04",
    "low": "#34a853",
    "info": "#4285f4",
}


class FindingDetailRenderer(BaseTemplateRenderer):
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

        divider = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0.75),
            Inches(0.72),
            Inches(slide_width - 1.5),
            Inches(0.08),
        )
        divider.fill.solid()
        divider.fill.fore_color.rgb = secondary
        divider.line.fill.background()

        title_text = content.get("title", "Finding")
        title_box = slide.shapes.add_textbox(
            Inches(0.8),
            Inches(0.95),
            Inches(slide_width - 3.0),
            Inches(0.9),
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

        severity = content.get("severity", "info").lower()
        sev_color = hex_to_rgb(_SEVERITY_COLORS.get(severity, "#4285f4"))
        badge = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
            Inches(slide_width - 2.2),
            Inches(0.98),
            Inches(1.45),
            Inches(0.42),
        )
        badge.fill.solid()
        badge.fill.fore_color.rgb = sev_color
        badge.line.fill.background()
        badge_tf = badge.text_frame
        badge_tf.paragraphs[0].text = severity.upper()
        badge_tf.paragraphs[0].font.size = Pt(12)
        badge_tf.paragraphs[0].font.bold = True
        badge_tf.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        badge_tf.paragraphs[0].font.name = font_family
        badge_tf.paragraphs[0].alignment = PP_ALIGN.CENTER

        left_x = 0.8
        left_width = 4.1
        image_x = left_x + left_width + 0.45
        image_width = slide_width - image_x - 0.8
        image_left = image_x
        image_top = 1.75
        image_height = 4.35
        label_top = 1.9
        if image_placement == IMAGE_PLACEMENT_FULL_BLEED:
            image_left = image_x - 0.08
            image_top = 1.45
            image_width = slide_width - image_left
            image_height = slide_height - image_top - 0.35
            label_top = 1.08

        desc_panel = self._add_panel(
            slide,
            left=left_x,
            top=1.75,
            width=left_width,
            height=2.55,
            fill_color=RGBColor(0xFF, 0xFF, 0xFF),
            line_color=RGBColor(0xDA, 0xE2, 0xEA),
            rounded=True,
        )
        desc_panel.shadow.inherit = False

        description = content.get("description", "")
        if description:
            desc_box = slide.shapes.add_textbox(
                Inches(left_x + 0.18),
                Inches(1.98),
                Inches(left_width - 0.36),
                Inches(2.1),
            )
            tf_desc = desc_box.text_frame
            tf_desc.word_wrap = True
            tf_desc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p_desc = tf_desc.paragraphs[0]
            p_desc.text = description
            p_desc.font.size = Pt(13)
            p_desc.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            p_desc.font.name = font_family

        recommendation = content.get("recommendation", "")
        if recommendation:
            rec_panel = self._add_panel(
                slide,
                left=left_x,
                top=4.55,
                width=left_width,
                height=1.55,
                fill_color=RGBColor(0xF1, 0xF7, 0xF2),
                line_color=secondary,
                rounded=True,
            )
            rec_panel.shadow.inherit = False
            rec_box = slide.shapes.add_textbox(
                Inches(left_x + 0.2),
                Inches(4.72),
                Inches(left_width - 0.4),
                Inches(1.18),
            )
            tf_rec = rec_box.text_frame
            tf_rec.word_wrap = True
            tf_rec.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p_hdr = tf_rec.paragraphs[0]
            p_hdr.text = "Recommendation"
            p_hdr.font.size = Pt(14)
            p_hdr.font.bold = True
            p_hdr.font.color.rgb = secondary
            p_hdr.font.name = font_family

            p_body = tf_rec.add_paragraph()
            p_body.text = recommendation
            p_body.font.size = Pt(12)
            p_body.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            p_body.font.name = font_family
            p_body.space_before = Pt(6)

        screenshot_url = content.get("screenshot_url", "")

        label_box = slide.shapes.add_textbox(
            Inches(image_left + 0.18),
            Inches(label_top),
            Inches(max(image_width - 0.36, 1.0)),
            Inches(0.3),
        )
        label_tf = label_box.text_frame
        label_p = label_tf.paragraphs[0]
        label_p.text = "Evidence"
        label_p.font.size = Pt(11)
        label_p.font.bold = True
        label_p.font.color.rgb = RGBColor(0x6B, 0x77, 0x86)
        label_p.font.name = font_family

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

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
