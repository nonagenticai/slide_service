from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .base import BaseTemplateRenderer

# Layout constants (inches) -- tuned for 13.333 x 7.5 widescreen
_TITLE_FONT_SIZE = Pt(36)
_BODY_FONT_SIZE = Pt(18)
_BUTTON_FONT_SIZE = Pt(18)
_URL_FONT_SIZE = Pt(14)
_CONTACT_FONT_SIZE = Pt(13)

_BUTTON_WIDTH = 4.6
_BUTTON_HEIGHT = 0.72
_BUTTON_CORNER_SIZE = 50000  # EMUs -- controls rounded-rect radius

_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_WHITE_85 = RGBColor(0xD9, 0xD9, 0xD9)  # ~85% white for subtle transparency feel


class CTARenderer(BaseTemplateRenderer):
    """Clean, bold closing slide -- conference-keynote style."""

    def render(
        self,
        prs: Presentation,
        content: dict,
        brand: dict,
        styling_rules: dict,
    ) -> Slide:
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)

        primary, _secondary = self._get_brand_colors(brand)
        font_family = self._get_font(brand)
        slide_w, slide_h = self._slide_size_inches(prs)

        # -- full-bleed primary background --------------------------------
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = primary

        # -- vertical layout bookkeeping ----------------------------------
        # Distribute elements from top to bottom with generous spacing.
        # All elements are horizontally centered.
        content_margin = 1.2  # left/right margin for text boxes
        text_width = slide_w - content_margin * 2

        has_body = bool(content.get("body"))
        has_cta = bool(content.get("cta_text"))
        has_url = bool(content.get("cta_url"))
        has_contact = bool(content.get("contact_info"))

        # Build a vertical stack and compute a balanced starting Y.
        # Heights: title 1.0, body 0.9, gap 0.35, button 0.72, url 0.45, contact 0.45
        total_h = 1.0  # title always present
        if has_body:
            total_h += 0.30 + 0.9  # gap + body
        if has_cta:
            total_h += 0.45 + _BUTTON_HEIGHT  # gap + button
        if has_url:
            total_h += 0.25 + 0.45  # gap + url
        if has_contact:
            total_h += 0.25 + 0.45  # gap + contact

        cursor_y = max((slide_h - total_h) / 2, 0.6)

        # -- title --------------------------------------------------------
        title_text = content.get("title", "Next Steps")
        title_box = slide.shapes.add_textbox(
            Inches(content_margin),
            Inches(cursor_y),
            Inches(text_width),
            Inches(1.0),
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = _TITLE_FONT_SIZE
        p.font.bold = True
        p.font.color.rgb = _WHITE
        p.font.name = font_family
        p.alignment = PP_ALIGN.CENTER
        cursor_y += 1.0

        # -- body ---------------------------------------------------------
        if has_body:
            cursor_y += 0.30
            body_box = slide.shapes.add_textbox(
                Inches(content_margin + 0.6),
                Inches(cursor_y),
                Inches(text_width - 1.2),
                Inches(0.9),
            )
            b_tf = body_box.text_frame
            b_tf.word_wrap = True
            b_tf.vertical_anchor = MSO_VERTICAL_ANCHOR.TOP
            b_p = b_tf.paragraphs[0]
            b_p.text = content["body"]
            b_p.font.size = _BODY_FONT_SIZE
            b_p.font.color.rgb = _WHITE_85
            b_p.font.name = font_family
            b_p.alignment = PP_ALIGN.CENTER
            cursor_y += 0.9

        # -- CTA button ---------------------------------------------------
        if has_cta:
            cursor_y += 0.45
            btn_left = (slide_w - _BUTTON_WIDTH) / 2
            btn = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
                Inches(btn_left),
                Inches(cursor_y),
                Inches(_BUTTON_WIDTH),
                Inches(_BUTTON_HEIGHT),
            )
            # White pill button with primary-color text (inverted)
            btn.fill.solid()
            btn.fill.fore_color.rgb = _WHITE
            btn.line.fill.background()
            # Increase corner rounding for a pill-like shape
            if btn.adjustments:
                btn.adjustments[0] = _BUTTON_CORNER_SIZE

            btn_tf = btn.text_frame
            btn_tf.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE
            btn_p = btn_tf.paragraphs[0]
            btn_p.text = content["cta_text"]
            btn_p.font.size = _BUTTON_FONT_SIZE
            btn_p.font.bold = True
            btn_p.font.color.rgb = primary
            btn_p.font.name = font_family
            btn_p.alignment = PP_ALIGN.CENTER
            cursor_y += _BUTTON_HEIGHT

        # -- URL ----------------------------------------------------------
        if has_url:
            cursor_y += 0.25
            url_box = slide.shapes.add_textbox(
                Inches(content_margin),
                Inches(cursor_y),
                Inches(text_width),
                Inches(0.45),
            )
            u_tf = url_box.text_frame
            u_tf.word_wrap = True
            u_p = u_tf.paragraphs[0]
            u_p.text = content["cta_url"]
            u_p.font.size = _URL_FONT_SIZE
            u_p.font.color.rgb = _WHITE
            u_p.font.name = font_family
            u_p.alignment = PP_ALIGN.CENTER
            cursor_y += 0.45

        # -- contact info -------------------------------------------------
        if has_contact:
            cursor_y += 0.25
            contact_box = slide.shapes.add_textbox(
                Inches(content_margin),
                Inches(cursor_y),
                Inches(text_width),
                Inches(0.45),
            )
            c_tf = contact_box.text_frame
            c_tf.word_wrap = True
            c_p = c_tf.paragraphs[0]
            c_p.text = content["contact_info"]
            c_p.font.size = _CONTACT_FONT_SIZE
            c_p.font.color.rgb = _WHITE_85
            c_p.font.name = font_family
            c_p.alignment = PP_ALIGN.CENTER

        # -- speaker notes ------------------------------------------------
        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
