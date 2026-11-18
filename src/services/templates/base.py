from __future__ import annotations

import io
import logging
from abc import ABC, abstractmethod
from typing import Any

from PIL import Image as PILImage
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.presentation import Presentation
from pptx.slide import Slide
from pptx.util import Inches, Pt

from src.services.image_layout import (
    IMAGE_FIT_CONTAIN,
    IMAGE_PLACEMENT_FRAME,
    resolve_asset_layout,
)
from src.services.image_resolver import load_image_bytes

logger = logging.getLogger(__name__)
_EMU_PER_INCH = 914400


def _parse_hex(value: str | None) -> RGBColor | None:
    """Parse ``#rrggbb`` / ``rrggbb`` / 3-digit ``#rgb`` → RGBColor, else None."""
    normalized = (value or "").strip().lstrip("#")
    if len(normalized) == 3:
        normalized = "".join(ch * 2 for ch in normalized)
    if len(normalized) != 6:
        return None
    try:
        r, g, b = (int(normalized[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return RGBColor(r, g, b)


def hex_to_rgb(hex_color: str, default: str = "#1a73e8") -> RGBColor:
    """Convert a hex color string to an ``RGBColor``, tolerating bad input.

    Accepts ``#rrggbb``, ``rrggbb`` and the 3-digit shorthand ``#rgb``. A
    malformed value (empty, a CSS name like ``red``, wrong length, non-hex)
    falls back to *default* instead of raising: a single bad brand color must
    not abort the whole PPTX deck. The HTML render path is already tolerant of
    such values, so the PPTX path was the asymmetric failure.
    """
    parsed = _parse_hex(hex_color)
    if parsed is not None:
        return parsed
    logger.warning("Invalid brand hex color %r; falling back to %s", hex_color, default)
    fallback = _parse_hex(default)
    return fallback if fallback is not None else RGBColor(0x1A, 0x73, 0xE8)


def as_text(value: object, default: str = "") -> str:
    """Coerce a caller-supplied value to slide text without painting ``"None"``.

    ``python-pptx`` raises ``TypeError`` when a non-``str`` is assigned to
    ``paragraph.text``, so every LLM-supplied field that reaches a text sink has
    to be coerced. Bare ``str(value)`` does stop the crash, but it swaps it for a
    cosmetic lie: a null ``trend_description`` becomes the *truthy* string
    ``"None"`` and gets rendered as body copy on the finished deck. Treating
    ``None`` as absent -- returning *default* -- is the coercion that is safe in
    both directions.
    """
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


class BaseTemplateRenderer(ABC):
    @abstractmethod
    def render(
        self,
        prs: Presentation,
        content: dict,
        brand: dict,
        styling_rules: dict,
    ) -> Slide: ...

    def _get_brand_colors(self, brand: dict) -> tuple[RGBColor, RGBColor]:
        primary = hex_to_rgb(brand.get("primary_color", "#1a73e8"), default="#1a73e8")
        secondary = hex_to_rgb(brand.get("secondary_color", "#34a853"), default="#34a853")
        return primary, secondary

    def _get_font(self, brand: dict) -> str:
        return brand.get("font_family", "Arial")

    def _collect_dict_items(
        self,
        raw: object,
        *,
        field: str,
        layout: str,
    ) -> tuple[list[dict], int]:
        """Reduce an LLM-supplied list to the elements that are actually dicts.

        Returns ``(items, dropped)`` and never raises. ``content[<field>]`` comes
        straight from model output, so ``["revenue", "churn"]`` (object wrapper
        forgotten), ``[null]``, or a bare ``None`` in place of the list are all
        routine shapes. Each one used to reach a per-element ``.get(...)`` or
        ``enumerate(...)`` and abort the *whole* deck build -- the render loop in
        ``deck_builder`` has no per-slide guard.

        Dropping is deliberately NOT silent. Every discarded element emits a
        WARNING naming the layout, the field and the count, so a payload that is
        malformed end to end is visible in logs instead of rendering as a tidy,
        successful, empty panel. Callers pair this with
        ``_add_degraded_marker`` to put the same fact on the slide itself.
        """
        if raw is None:
            return [], 0
        if not isinstance(raw, list):
            logger.warning(
                "%s: discarding non-list %r payload of type %s; expected a list of objects",
                layout,
                field,
                type(raw).__name__,
            )
            return [], 1
        items = [item for item in raw if isinstance(item, dict)]
        dropped = len(raw) - len(items)
        if dropped:
            logger.warning(
                "%s: dropped %d of %d malformed %r entries (non-dict elements); "
                "rendering the %d valid one(s)",
                layout,
                dropped,
                len(raw),
                field,
                len(items),
            )
        return items, dropped

    def _collect_list_entries(
        self,
        raw: object,
        *,
        field: str,
        layout: str,
    ) -> tuple[list[Any], int]:
        """Guard the LIST SHAPE of a slot, keeping every element it contains.

        The sibling of :meth:`_collect_dict_items` for the families that
        legitimately render scalars: agenda ``items``, roadmap ``phases``,
        process_flow ``steps``, feature ``bullets`` and two_up column bullets all
        accept ``["Intro", "Body"]`` as well as ``[{"text": ...}]``, and each one
        already stringifies a non-dict element. Filtering those to dicts would
        DESTROY working decks, so this helper deliberately does not.

        What it guards is the shape the templates were blind to: the slot itself
        arriving as ``None``, a bare string (which ``for item in raw`` iterates
        per CHARACTER) or a dict (which iterates KEYS). Each used to render as a
        tidy, successful, empty panel. Returns ``(entries, dropped)`` and never
        raises; a non-list payload counts as ONE loss, because the payload asked
        for a panel the slide will not have.

        Semantics are copied from ``html_slide_renderer._entries`` so the PPTX
        slide and its PNG preview count the same losses. Callers pair this with
        :meth:`_add_degraded_marker`.
        """
        if raw is None:
            return [], 0
        if not isinstance(raw, list | tuple):
            logger.warning(
                "%s: discarding non-list %r payload of type %s; expected a list",
                layout,
                field,
                type(raw).__name__,
            )
            return [], 1
        return list(raw), 0

    def _add_degraded_marker(
        self,
        slide: Slide,
        *,
        dropped: int,
        noun: str,
        font_family: str,
        top: float,
        left: float = 0.5,
        width: float = 9.0,
    ) -> None:
        """Put "N <noun> could not be displayed" on a slide that lost entries.

        Without this a fully-malformed payload renders as a *perfect* empty
        panel: no exception, no missing slide, nothing for a reviewer or a
        downstream consumer to catch. The marker is what stops "every entry was
        destroyed" from being indistinguishable from "there were none".
        """
        if dropped <= 0:
            return
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(0.4))
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = f"⚠ {dropped} {noun} could not be displayed (malformed input)"
        p.font.size = Pt(11)
        p.font.italic = True
        p.font.color.rgb = RGBColor(0xB3, 0x26, 0x1E)
        p.font.name = font_family
        p.alignment = PP_ALIGN.CENTER

    def _add_speaker_notes(self, slide: Slide, notes: str) -> None:
        if notes:
            notes_slide = slide.notes_slide
            tf = notes_slide.notes_text_frame
            if tf is not None:
                tf.text = notes

    def _slide_size_inches(self, prs: Presentation) -> tuple[float, float]:
        width = prs.slide_width
        height = prs.slide_height
        if width is None or height is None:
            msg = "Presentation has no slide dimensions set"
            raise ValueError(msg)
        return width / _EMU_PER_INCH, height / _EMU_PER_INCH

    def _add_panel(
        self,
        slide: Slide,
        *,
        left: float,
        top: float,
        width: float,
        height: float,
        fill_color: RGBColor,
        line_color: RGBColor | None = None,
        rounded: bool = True,
    ) -> Any:
        shape_type = (
            MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE if rounded else MSO_AUTO_SHAPE_TYPE.RECTANGLE
        )
        panel = slide.shapes.add_shape(
            shape_type,
            Inches(left),
            Inches(top),
            Inches(width),
            Inches(height),
        )
        panel.fill.solid()
        panel.fill.fore_color.rgb = fill_color
        if line_color is None:
            panel.line.fill.background()
        else:
            panel.line.color.rgb = line_color
            panel.line.width = Pt(1.2)
        return panel

    def _load_image_bytes(self, image_ref: str) -> bytes | None:
        return load_image_bytes(image_ref)

    def _add_image_placeholder(
        self,
        slide: Slide,
        *,
        left: float,
        top: float,
        width: float,
        height: float,
        font_family: str,
        label: str,
    ) -> None:
        placeholder = self._add_panel(
            slide,
            left=left,
            top=top,
            width=width,
            height=height,
            fill_color=RGBColor(0xF3, 0xF5, 0xF7),
            line_color=RGBColor(0xD3, 0xD9, 0xE1),
            rounded=False,
        )
        tf = placeholder.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        tf.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.text = label
        p.alignment = PP_ALIGN.CENTER
        if p.runs:
            run = p.runs[0]
            run.font.size = Pt(11)
            run.font.italic = True
            run.font.color.rgb = RGBColor(0x7A, 0x86, 0x94)
            run.font.name = font_family

    def _add_cover_image(
        self,
        slide: Slide,
        *,
        image_ref: str,
        left: float,
        top: float,
        width: float,
        height: float,
        font_family: str,
        placeholder_label: str,
        fit_mode: str,
    ) -> Any:
        image_bytes = self._load_image_bytes(image_ref)
        if not image_bytes:
            self._add_image_placeholder(
                slide,
                left=left,
                top=top,
                width=width,
                height=height,
                font_family=font_family,
                label=placeholder_label,
            )
            return None

        frame_ratio = width / height if height else 1.0
        try:
            with PILImage.open(io.BytesIO(image_bytes)) as image:
                width_px, height_px = image.size
        except OSError, RuntimeError:
            logger.warning("Failed to inspect image payload", exc_info=True)
            self._add_image_placeholder(
                slide,
                left=left,
                top=top,
                width=width,
                height=height,
                font_family=font_family,
                label=placeholder_label,
            )
            return None

        image_ratio = width_px / height_px if height_px else frame_ratio
        if fit_mode == IMAGE_FIT_CONTAIN:
            if width_px <= 0 or height_px <= 0:
                return None
            scale = min(width / width_px, height / height_px)
            render_width = max(width_px * scale, 0.05)
            render_height = max(height_px * scale, 0.05)
            return slide.shapes.add_picture(
                io.BytesIO(image_bytes),
                Inches(left + (width - render_width) / 2),
                Inches(top + (height - render_height) / 2),
                Inches(render_width),
                Inches(render_height),
            )

        pic = slide.shapes.add_picture(
            io.BytesIO(image_bytes),
            Inches(left),
            Inches(top),
            Inches(width),
            Inches(height),
        )

        if image_ratio > frame_ratio:
            scaled_width = height * image_ratio
            overflow = max(scaled_width - width, 0.0)
            if scaled_width > 0:
                crop = overflow / scaled_width / 2
                pic.crop_left = crop
                pic.crop_right = crop
        else:
            scaled_height = width / image_ratio if image_ratio else height
            overflow = max(scaled_height - height, 0.0)
            if scaled_height > 0:
                crop = overflow / scaled_height / 2
                pic.crop_top = crop
                pic.crop_bottom = crop

        return pic

    def _resolve_asset_layout(
        self,
        content: dict[str, Any],
        styling_rules: dict[str, Any],
        slot_name: str,
    ) -> tuple[str, str]:
        return resolve_asset_layout(content, styling_rules, slot_name=slot_name)

    def _add_treated_image(
        self,
        slide: Slide,
        *,
        image_ref: str,
        left: float,
        top: float,
        width: float,
        height: float,
        font_family: str,
        placeholder_label: str,
        placement: str = IMAGE_PLACEMENT_FRAME,
        frame_fill_color: RGBColor | None = None,
        frame_line_color: RGBColor | None = None,
        frame_padding: float = 0.14,
        rounded: bool = True,
        fit_mode: str,
    ) -> Any:
        if placement != IMAGE_PLACEMENT_FRAME:
            return self._add_cover_image(
                slide,
                image_ref=image_ref,
                left=left,
                top=top,
                width=width,
                height=height,
                font_family=font_family,
                placeholder_label=placeholder_label,
                fit_mode=fit_mode,
            )

        panel = self._add_panel(
            slide,
            left=left,
            top=top,
            width=width,
            height=height,
            fill_color=frame_fill_color or RGBColor(0xFF, 0xFF, 0xFF),
            line_color=frame_line_color or RGBColor(0xDA, 0xE2, 0xEA),
            rounded=rounded,
        )
        panel.shadow.inherit = False
        return self._add_cover_image(
            slide,
            image_ref=image_ref,
            left=left + frame_padding,
            top=top + frame_padding,
            width=max(width - frame_padding * 2, 0.2),
            height=max(height - frame_padding * 2, 0.2),
            font_family=font_family,
            placeholder_label=placeholder_label,
            fit_mode=fit_mode,
        )
