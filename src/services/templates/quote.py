from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer

_DARK = RGBColor(0x0B, 0x1F, 0x3A)
_MUTED = RGBColor(0x6B, 0x77, 0x85)


class QuoteRenderer(BaseTemplateRenderer):
    """Editorial pull-quote slide: big centered quotation with attribution."""

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

        # -- Full-bleed light background panel --------------------------------
        bg = self._add_panel(
            slide,
            left=0.0,
            top=0.0,
            width=slide_w,
            height=slide_h,
            fill_color=RGBColor(0xF8, 0xFA, 0xFC),
            rounded=False,
        )
        bg.line.fill.background()
        bg.shadow.inherit = False

        quote_text = content.get("quote") or content.get("text") or ""
        attribution = (
            content.get("attribution") or content.get("author") or content.get("name") or ""
        )
        role = content.get("role") or content.get("source") or content.get("company") or ""

        margin_x = 1.6
        text_width = slide_w - margin_x * 2

        # -- Large decorative quotation mark (accent) -------------------------
        mark_box = slide.shapes.add_textbox(
            Inches(margin_x),
            Inches(slide_h * 0.16),
            Inches(2.0),
            Inches(1.6),
        )
        mark_tf = mark_box.text_frame
        mark_tf.word_wrap = True
        mark_p = mark_tf.paragraphs[0]
        mark_p.text = "“"
        mark_p.font.size = Pt(140)
        mark_p.font.bold = True
        mark_p.font.color.rgb = primary
        mark_p.font.name = font_family
        mark_p.alignment = PP_ALIGN.LEFT

        # -- Quote text (centered, large, dark) -------------------------------
        if quote_text:
            quote_font_size = 34 if len(quote_text) > 160 else (40 if len(quote_text) > 90 else 46)
            quote_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(slide_h * 0.30),
                Inches(text_width),
                Inches(slide_h * 0.34),
            )
            q_tf = quote_box.text_frame
            q_tf.word_wrap = True
            q_p = q_tf.paragraphs[0]
            q_p.text = quote_text
            q_p.font.size = Pt(quote_font_size)
            q_p.font.bold = True
            q_p.font.color.rgb = _DARK
            q_p.font.name = font_family
            q_p.alignment = PP_ALIGN.CENTER

        # -- Accent rule beneath the quote ------------------------------------
        rule_width = 1.4
        rule_top = slide_h * 0.70
        rule = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches((slide_w - rule_width) / 2),
            Inches(rule_top),
            Inches(rule_width),
            Inches(0.03),
        )
        rule.fill.solid()
        rule.fill.fore_color.rgb = secondary
        rule.line.fill.background()
        rule.shadow.inherit = False

        # -- Attribution + role (muted gray) ----------------------------------
        if attribution:
            attr_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(rule_top + 0.2),
                Inches(text_width),
                Inches(0.6),
            )
            a_tf = attr_box.text_frame
            a_tf.word_wrap = True
            a_p = a_tf.paragraphs[0]
            a_p.text = attribution
            a_p.font.size = Pt(22)
            a_p.font.bold = True
            a_p.font.color.rgb = _DARK
            a_p.font.name = font_family
            a_p.alignment = PP_ALIGN.CENTER

        if role:
            role_box = slide.shapes.add_textbox(
                Inches(margin_x),
                Inches(rule_top + (0.85 if attribution else 0.2)),
                Inches(text_width),
                Inches(0.5),
            )
            r_tf = role_box.text_frame
            r_tf.word_wrap = True
            r_p = r_tf.paragraphs[0]
            r_p.text = role
            r_p.font.size = Pt(16)
            r_p.font.color.rgb = _MUTED
            r_p.font.name = font_family
            r_p.alignment = PP_ALIGN.CENTER

        # -- Speaker notes ----------------------------------------------------
        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
