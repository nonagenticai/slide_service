from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer


class StatRenderer(BaseTemplateRenderer):
    """Full-bleed slide featuring a single oversized hero statistic."""

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
        # Drawn unconditionally so an all-empty content still yields >=1 shape.
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

        # -- Content contract (all optional) ----------------------------------
        # Coerce to str: a stat's `value` is a number in intent (94, 3.2) and an
        # int payload would otherwise blow up on len()/pptx run-text assignment.
        value_text = str(
            content.get("value")
            or content.get("stat")
            or content.get("number")
            or content.get("metric")
            or ""
        )
        label_text = str(content.get("label") or content.get("title") or "")
        caption_text = str(
            content.get("caption")
            or content.get("context")
            or content.get("description")
            or content.get("subtitle")
            or ""
        )

        # -- Layout geometry --------------------------------------------------
        margin_x = 1.0
        text_width = slide_w - margin_x * 2

        value_box_h = 3.0
        value_gap = 0.2
        label_box_h = 1.0
        label_gap = 0.25
        caption_box_h = 1.2

        block_h = value_box_h
        if label_text:
            block_h += value_gap + label_box_h
        if caption_text:
            block_h += label_gap + caption_box_h
        top_offset = max((slide_h - block_h) / 2, 0.0)

        # -- Hero value (very large, bold, brand secondary/accent) ------------
        cursor = top_offset
        if value_text:
            value_font_size = (
                160 if len(value_text) <= 5 else (110 if len(value_text) <= 10 else 72)
            )
            value_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(cursor),
                Inches(text_width),
                Inches(value_box_h),
            )
            tf = value_box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = value_text
            p.font.size = Pt(value_font_size)
            p.font.bold = True
            p.font.color.rgb = secondary
            p.font.name = font_family
            p.alignment = PP_ALIGN.CENTER
        cursor += value_box_h

        # -- Label beneath, white ---------------------------------------------
        if label_text:
            cursor += value_gap
            label_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(cursor),
                Inches(text_width),
                Inches(label_box_h),
            )
            tf_label = label_box.text_frame
            tf_label.word_wrap = True
            p_label = tf_label.paragraphs[0]
            p_label.text = label_text
            p_label.font.size = Pt(28)
            p_label.font.bold = True
            p_label.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            p_label.font.name = font_family
            p_label.alignment = PP_ALIGN.CENTER
            cursor += label_box_h

        # -- Caption, smaller, light-gray -------------------------------------
        if caption_text:
            cursor += label_gap
            caption_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(cursor),
                Inches(text_width),
                Inches(caption_box_h),
            )
            tf_caption = caption_box.text_frame
            tf_caption.word_wrap = True
            p_caption = tf_caption.paragraphs[0]
            p_caption.text = caption_text
            p_caption.font.size = Pt(18)
            p_caption.font.color.rgb = RGBColor(0xC8, 0xCD, 0xD3)
            p_caption.font.name = font_family
            p_caption.alignment = PP_ALIGN.CENTER

        # -- Speaker notes ----------------------------------------------------
        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
