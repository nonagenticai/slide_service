from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer


class TitleRenderer(BaseTemplateRenderer):
    """Clean, full-bleed title slide inspired by Apple/Stripe keynotes."""

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

        # -- Full-bleed hero background in brand primary ----------------------
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

        # -- Layout geometry --------------------------------------------------
        # Title and subtitle are centered horizontally with generous margins.
        margin_x = 1.4
        text_width = slide_w - margin_x * 2

        title_text = content.get("title", "")
        subtitle_text = content.get("subtitle", "")

        # Vertical centering: compute combined text block height, then offset.
        title_font_size = 32 if len(title_text) > 90 else (40 if len(title_text) > 50 else 46)
        title_box_h = 2.2 if title_font_size <= 32 else 1.8
        accent_h = 0.02
        accent_gap = 0.35
        subtitle_box_h = 1.0

        block_h = title_box_h + accent_gap + accent_h + accent_gap + subtitle_box_h
        top_offset = (slide_h - block_h) / 2

        # -- Title text -------------------------------------------------------
        title_top = top_offset
        title_box = slide.shapes.add_textbox(
            Inches(margin_x),
            Inches(title_top),
            Inches(text_width),
            Inches(title_box_h),
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(title_font_size)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        p.font.name = font_family
        p.alignment = PP_ALIGN.CENTER

        # -- Thin accent separator (secondary color) -------------------------
        accent_top = title_top + title_box_h + accent_gap
        accent_width = 1.6
        accent_left = (slide_w - accent_width) / 2

        accent_line = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(accent_left),
            Inches(accent_top),
            Inches(accent_width),
            Inches(accent_h),
        )
        accent_line.fill.solid()
        accent_line.fill.fore_color.rgb = secondary
        accent_line.line.fill.background()

        # -- Subtitle text ----------------------------------------------------
        if subtitle_text:
            subtitle_top = accent_top + accent_h + accent_gap
            sub_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(subtitle_top),
                Inches(text_width),
                Inches(subtitle_box_h),
            )
            tf_sub = sub_box.text_frame
            tf_sub.word_wrap = True
            p_sub = tf_sub.paragraphs[0]
            p_sub.text = subtitle_text
            p_sub.font.size = Pt(20)
            p_sub.font.color.rgb = RGBColor(0xE8, 0xEA, 0xED)
            p_sub.font.name = font_family
            p_sub.alignment = PP_ALIGN.CENTER

        # -- Speaker notes ----------------------------------------------------
        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
