from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from src.services.image_layout import IMAGE_PLACEMENT_FULL_BLEED

from .base import BaseTemplateRenderer


class FeatureSpotlightRenderer(BaseTemplateRenderer):
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
            "image_url",
        )

        # -- Background: light off-white --
        bg = slide.background
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(0xF8, 0xFA, 0xFB)

        # -- Layout constants --
        margin_left = 0.7
        margin_right = 0.7
        usable_width = slide_width - margin_left - margin_right
        title_top = 0.55
        title_height = 0.65
        body_top = title_top + title_height + 0.15  # 1.35
        body_bottom = slide_height - 0.5
        body_height = body_bottom - body_top

        image_url = content.get("image_url", "")
        has_image = bool(image_url and image_url.strip())

        # Determine text content area width based on image presence
        image_area_left = 0.0
        image_area_width = 0.0
        if has_image:
            text_width = usable_width * 0.58
            image_area_left = margin_left + text_width + 0.3
            image_area_width = slide_width - image_area_left - margin_right
        else:
            text_width = usable_width

        # -- Thin accent bar under title --
        accent_bar_top = title_top + title_height + 0.02
        accent_bar = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(margin_left),
            Inches(accent_bar_top),
            Inches(1.4),
            Inches(0.04),
        )
        accent_bar.fill.solid()
        # Decorative accent uses brand `secondary` so it differentiates from the
        # primary-coloured title above it (matches scorecard `header_rule` +
        # finding_detail `divider` pattern). Was `primary` — invisible against
        # the title, and triggered RUF059 because `secondary` was never used.
        accent_bar.fill.fore_color.rgb = secondary
        accent_bar.line.fill.background()

        # -- Title --
        title_text = content.get("title", "Feature Spotlight")
        title_box = slide.shapes.add_textbox(
            Inches(margin_left),
            Inches(title_top),
            Inches(text_width),
            Inches(title_height),
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(30)
        p.font.bold = True
        p.font.color.rgb = primary
        p.font.name = font_family
        p.alignment = PP_ALIGN.LEFT

        # -- Description --
        description = content.get("description", "")
        desc_bottom = body_top
        if description:
            desc_top = body_top
            desc_height = min(1.2, body_height * 0.35)
            desc_box = slide.shapes.add_textbox(
                Inches(margin_left),
                Inches(desc_top),
                Inches(text_width),
                Inches(desc_height),
            )
            tf_desc = desc_box.text_frame
            tf_desc.word_wrap = True
            tf_desc.auto_size = MSO_AUTO_SIZE.NONE
            p_desc = tf_desc.paragraphs[0]
            p_desc.text = description
            p_desc.font.size = Pt(15)
            p_desc.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            p_desc.font.name = font_family
            p_desc.space_after = Pt(6)
            p_desc.alignment = PP_ALIGN.LEFT
            desc_bottom = desc_top + desc_height + 0.2

        # -- Bullets --
        # Element shapes are guarded below; the list itself was not. A dict
        # payload reached `bullets[:max_bullets]` -> "TypeError: unhashable type:
        # 'slice'", aborting the whole deck.
        bullets, dropped = self._collect_list_entries(
            content.get("bullets"), field="bullets", layout="feature_spotlight"
        )
        bullet_bottom = desc_bottom
        if bullets:
            bullet_top = desc_bottom
            bullet_spacing = 0.48
            max_bullets = min(len(bullets), 6)
            available_height = body_bottom - bullet_top
            if max_bullets * bullet_spacing > available_height:
                bullet_spacing = available_height / max_bullets

            for idx, bullet in enumerate(bullets[:max_bullets]):
                bullet_text = (
                    bullet.get("text", str(bullet)) if isinstance(bullet, dict) else str(bullet)
                )
                y = bullet_top + idx * bullet_spacing

                # Colored dot
                dot_size = 0.09
                dot_y = y + 0.1  # vertically center with text
                dot = slide.shapes.add_shape(
                    MSO_AUTO_SHAPE_TYPE.OVAL,
                    Inches(margin_left + 0.05),
                    Inches(dot_y),
                    Inches(dot_size),
                    Inches(dot_size),
                )
                dot.fill.solid()
                dot.fill.fore_color.rgb = primary
                dot.line.fill.background()

                # Bullet text
                bullet_box = slide.shapes.add_textbox(
                    Inches(margin_left + 0.3),
                    Inches(y),
                    Inches(text_width - 0.3),
                    Inches(bullet_spacing - 0.04),
                )
                tf_bullet = bullet_box.text_frame
                tf_bullet.word_wrap = True
                tf_bullet.auto_size = MSO_AUTO_SIZE.NONE
                bp = tf_bullet.paragraphs[0]
                bp.text = bullet_text
                bp.font.size = Pt(14)
                bp.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                bp.font.name = font_family
                bp.alignment = PP_ALIGN.LEFT

            bullet_bottom = bullet_top + max_bullets * bullet_spacing

        # A spotlight that lost its whole bullet payload otherwise renders as a
        # heading + description with a clean gap where the feature list belongs.
        self._add_degraded_marker(
            slide,
            dropped=dropped,
            noun="feature bullets",
            font_family=font_family,
            left=margin_left,
            width=text_width,
            top=min(bullet_bottom + 0.1, body_bottom),
        )

        # -- Image (only if URL is provided) --
        if has_image:
            img_top = body_top
            img_height = body_height

            if image_placement == IMAGE_PLACEMENT_FULL_BLEED:
                img_left = image_area_left - 0.1
                img_w = slide_width - img_left
                img_top = body_top - 0.1
                img_height = slide_height - img_top
            else:
                img_left = image_area_left
                img_w = image_area_width

            self._add_treated_image(
                slide,
                image_ref=image_url,
                left=img_left,
                top=img_top,
                width=img_w,
                height=img_height,
                font_family=font_family,
                # An empty label draws a blank placeholder box: the slide shows a
                # silent grey rectangle where the feature image should be, and a
                # reviewer cannot tell an expired presigned URL from a design
                # choice. Every sibling call site (comparison, finding_detail,
                # tutorial_step) names what is missing — do the same here.
                placeholder_label="Feature image unavailable",
                placement=image_placement,
                frame_fill_color=RGBColor(0xFF, 0xFF, 0xFF),
                frame_line_color=RGBColor(0xE4, 0xE8, 0xEC),
                fit_mode=image_fit,
            )

        self._add_speaker_notes(slide, content.get("notes", ""))
        return slide
