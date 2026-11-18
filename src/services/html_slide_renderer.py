"""Render deck slide previews as PNGs via HTML/CSS + headless Chromium.

Replaces the old PIL ``preview_renderer`` (hardcoded fonts, fixed 64pt size that
truncated with "…", URLs that broke mid-glyph and overflowed). CSS does the
fitting PIL could not: ``clamp()`` fluid sizes, ``text-wrap: balance`` and
``overflow-wrap: anywhere`` so a long title or a long URL wraps cleanly at the
box edge — the text fits by construction, never truncated.

The technique (lazy ``async_playwright``, lean Chromium flags, a bounded
long-lived browser pool) is **copied** from ``html_render_service`` but
slide_service owns it outright — no runtime dependency, so this service stays
independently usable. Slides render at a **fixed 1920x1080 viewport** with
``full_page=False``, which sidesteps html_render's tall-page OOM entirely (no
scroll-height bitmap is ever allocated).

``build_slide_html`` is a pure, sync, testable function (no browser, no I/O);
``render_slide_png`` resolves images off the event loop, builds the HTML, and
screenshots it through the shared browser pool.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.services.browser_pool import get_browser_pool
from src.services.image_layout import (
    IMAGE_FIT_CONTAIN,
    IMAGE_PLACEMENT_FULL_BLEED,
    ImageSlotKind,
    classify_image_slot,
    resolve_asset_layout,
)
from src.services.image_resolver import load_image_bytes

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slides"
_FONTS_DIR = _TEMPLATES_DIR / "fonts"

# Bundled variable-weight latin woff2 faces, keyed by canonical (lowercased)
# ``font-family`` name. A brand requests a display + body family by name; any name
# not bundled here falls back to the CSS stack (Inter/system) so an unknown brand
# font can't break the render. Add a face by dropping its ``*-latin.woff2`` here.
_FONT_FILES: dict[str, str] = {
    "inter": "Inter-latin.woff2",
    "fraunces": "Fraunces-latin.woff2",
    "ibm plex sans": "IBMPlexSans-latin.woff2",
}

# Viewport == output resolution. 1920x1080 matches the compositor's video frame,
# so a slide shot needs no rescale downstream.
_VIEWPORT_BY_RATIO: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "4:3": (1440, 1080),
}

# layout_type -> template family. The six original families mirror preview_renderer's
# dispatch; SLIDE-2 adds five token-driven families (quote/stat/section_divider/
# timeline/two_up). Three previously-unmapped layout_types the orchestrator already
# emits — `agenda`, `roadmap`, `process_flow` — silently fell back to the generic
# `title` family, dropping their list/phase/step content. They are re-routed here to
# the purpose-built families that actually render that content (a live-quality win
# with no orchestrator change).
_FAMILY_BY_LAYOUT: dict[str, str] = {
    "comparison": "comparison",
    "scorecard": "scorecard",
    "metric_summary": "scorecard",
    "finding_detail": "image_focus",
    "feature_spotlight": "image_focus",
    "tutorial_step": "image_focus",
    "cta": "cta",
    # SLIDE-2 families + their aliases
    "quote": "quote",
    "testimonial": "quote",
    "stat": "stat",
    "metric_hero": "stat",
    "section_divider": "section_divider",
    "agenda": "section_divider",  # re-route: was title fallback (dropped `items`)
    "timeline": "timeline",
    "roadmap": "timeline",  # re-route: was title fallback (dropped `phases`)
    "process_flow": "timeline",  # re-route: was title fallback (dropped `steps`)
    "two_up": "two_up",
    "split": "two_up",
}


# Design-token surface. A caller (today: the orchestrator's design system) may pass
# a flat {token_name: value} map; each entry is emitted as a CSS custom property on
# ``:root`` (``on_surface`` -> ``--on-surface``) and the slide templates consume it as
# ``var(--on-surface, <today's literal>)``. No tokens => every fallback wins => the
# render is byte-identical to the pre-token output. Both name and value are validated
# so a hostile/garbled token can never escape the declaration and rewrite the sheet.
_TOKEN_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_TOKEN_VALUE_RE = re.compile(r"^[A-Za-z0-9 ,.#%()/_+-]+$")


def _css_tokens(tokens: Mapping[str, str] | None) -> dict[str, str]:
    """Validate a design-token map into CSS-custom-property name/value pairs.

    Names are snake_case -> kebab-case (``on_surface`` -> ``on-surface``). Any entry
    with an unusable name, an empty value, or a value carrying CSS/HTML control
    characters (``{}`` ``;`` ``<>`` quotes) is dropped, not sanitised — the template
    then falls back to its literal, which is always a valid render.
    """
    safe: dict[str, str] = {}
    for raw_name, raw_value in (tokens or {}).items():
        name = str(raw_name or "").strip()
        value = "" if raw_value is None else str(raw_value).strip()
        if not name or not value:
            continue
        if not _TOKEN_NAME_RE.fullmatch(name) or not _TOKEN_VALUE_RE.fullmatch(value):
            continue
        safe[name.replace("_", "-").lower()] = value
    return safe


@lru_cache(maxsize=8)
def _font_data_uri(family: str) -> str:
    """Base64 ``data:`` URI for a bundled variable woff2 (latin), by family name.

    Returns ``""`` for any family not in ``_FONT_FILES`` (the template then omits
    its ``@font-face`` and the CSS stack falls back to Inter/system) so a brand
    asking for an unbundled font can never break the render.
    """
    fname = _FONT_FILES.get(str(family or "").strip().lower())
    if not fname:
        return ""
    try:
        raw = (_FONTS_DIR / fname).read_bytes()
    except OSError:
        logger.warning(
            "Bundled woff2 missing at %s — falling back to system fonts", _FONTS_DIR / fname
        )
        return ""
    return "data:font/woff2;base64," + base64.b64encode(raw).decode("ascii")


def _resolve_fonts(brand: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """``(display_font, body_font, display_uri, body_uri)``, warning on any substitution.

    Two-slot typography: a display face for titles, a body face for everything
    else, so a brand's headline font (e.g. Fraunces) actually loads instead of
    silently falling back. ``font_family`` is the display font (back-compat);
    ``body_font_family`` the body font (defaults to Inter when unset).

    Module-level rather than inline for the same reason as ``_finish``:
    ``build_slide_html`` keeps its C901 budget for the layout-family dispatch,
    which is the part that has to grow when a family is added.

    Warned from HERE, not from inside ``_font_data_uri``: that one is
    ``lru_cache``d, so a warning there would fire once per process and stay
    silent for every later deck asking for the same unbundled family.
    """
    display_font = str(brand.get("font_family") or "Inter")
    body_font = str(brand.get("body_font_family") or "Inter")
    display_uri = _font_data_uri(display_font)
    body_uri = _font_data_uri(body_font)
    _warn_unbundled_font("display", display_font, display_uri)
    if body_font != display_font:
        _warn_unbundled_font("body", body_font, body_uri)
    return display_font, body_font, display_uri, body_uri


def _warn_unbundled_font(slot: str, family: str, data_uri: str) -> None:
    """WARN when a brand font could not be embedded, so the substitution is not silent.

    ``_font_data_uri`` degrades an unbundled family to ``""`` so an unknown brand
    font can never break the render. That is the right behaviour, but on its own
    it was *invisible*: no log, no signal anywhere. The PNG then falls back down
    the CSS stack to Inter/system while the PPTX path (``BaseTemplateRenderer.
    _get_font``) sets the requested family on every run — so the two artifacts of
    the SAME deck disagree about the typeface, and which one is "right" depends
    only on which file you happen to open.

    This is deliberately a log and NOT another manifest counter: the six
    ``image_slots_*`` / ``content_entries_*`` counters already on the manifest
    are read by nobody, and a seventh would be one more unread number rather
    than a signal. An operator diagnosing "why does the deck look wrong" reads
    the pod log; the fix (bundle the woff2, or set a bundled family on the brand)
    is named in the message.
    """
    if data_uri:
        return
    logger.warning(
        "Brand %s font %r is not bundled — no @font-face is emitted, so this PNG "
        "renders the CSS fallback (Inter/system) while deck.pptx sets %r on every "
        "run: the two artifacts of the same deck disagree about the typeface. "
        "Bundled families: %s. Fix by bundling its woff2 in %s or by setting a "
        "bundled family on the brand.",
        slot,
        family,
        family,
        ", ".join(sorted(_FONT_FILES)),
        _FONTS_DIR,
    )


@lru_cache(maxsize=1)
def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(default=True, default_for_string=True),
    )


def _family(layout_type: str) -> str:
    return _FAMILY_BY_LAYOUT.get(str(layout_type or "title").strip().lower(), "title")


def _data_uri(image_bytes: bytes | None) -> str:
    if not image_bytes:
        return ""
    return "data:image/*;base64," + base64.b64encode(image_bytes).decode("ascii")


def _image_slot(
    content: dict[str, Any],
    slot_name: str,
    *,
    image_bytes: bytes | None,
) -> dict[str, Any] | None:
    """Build the template context for one image slot (data URI + fit/placement).

    ``object-fit: cover|contain`` maps 1:1 onto the existing IMAGE_FIT contract;
    ``full_bleed`` placement drops the white frame so the image reaches the edge.
    Returns ``None`` when no image ref was supplied (template draws a placeholder).
    """
    placement, fit = resolve_asset_layout(content, {}, slot_name=slot_name)
    return {
        "data_uri": _data_uri(image_bytes),
        "object_fit": "contain" if fit == IMAGE_FIT_CONTAIN else "cover",
        "full_bleed": placement == IMAGE_PLACEMENT_FULL_BLEED,
        "has_image": bool(image_bytes),
    }


def _string(content: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = content.get(key)
        if value not in (None, ""):
            return str(value)
    return default


@dataclass(frozen=True, slots=True)
class SlideContentReport:
    """How many list entries a slide OFFERED, and how many were unrenderable.

    The PNG twin of :class:`SlideImageReport`, and the PNG twin of the PPTX
    path's ``BaseTemplateRenderer._collect_dict_items`` / ``_add_degraded_marker``
    pair. Every normaliser below reduces LLM-supplied content to the elements it
    can actually draw; dropping is correct (one malformed metric must not abort a
    deck) but it used to be *silent*, so a scorecard whose entries were all
    wrong-shaped rendered as a perfect, empty, successful-looking frame.

    ``offered`` counts the entries the payload presented, ``dropped`` the subset
    that produced nothing renderable, ``fields`` names the slots they came from.
    ``dropped == 0`` is the healthy case and must stay indistinguishable from a
    slide that offered nothing at all — a text-only deck is legitimate.

    Over-limit truncation is deliberately NOT counted here: a family that shows
    six of eight metrics did what it was designed to do, and marking that deck
    "degraded" would be a false positive worse than the bug. It is logged
    instead (see ``_truncated``).
    """

    offered: int = 0
    dropped: int = 0
    fields: tuple[str, ...] = field(default=())

    def merge(self, other: SlideContentReport) -> SlideContentReport:
        return SlideContentReport(
            offered=self.offered + other.offered,
            dropped=self.dropped + other.dropped,
            fields=self.fields + other.fields,
        )

    @property
    def note(self) -> str:
        """The reviewer-facing marker text, or ``""`` when nothing was lost.

        Phrasing is copied verbatim from ``BaseTemplateRenderer._add_degraded_marker``
        so a PPTX slide and its PNG preview say the same sentence.
        """
        if self.dropped <= 0:
            return ""
        noun = ", ".join(dict.fromkeys(self.fields)) or "entries"
        return f"{self.dropped} {noun} could not be displayed (malformed input)"


def _truncated(field_name: str, kept: int, total: int) -> None:
    """Log an over-limit slot. Not a defect — but not silent either."""
    if total > kept:
        logger.info(
            "Slot %r offered %d entries; the layout shows %d — %d not displayed",
            field_name,
            total,
            kept,
            total - kept,
        )


def _entries(raw: object, field_name: str) -> tuple[list[object], int]:
    """Reduce a raw list slot to its elements, counting a non-list as one loss.

    ``content[<slot>]`` comes straight off a JSON body, so ``None``, a bare
    string (which ``for item in raw`` would happily iterate *per character*),
    and a dict are all routine shapes. Mirrors
    ``BaseTemplateRenderer._collect_dict_items``: never raises, never silent.
    """
    if raw is None:
        return [], 0
    if not isinstance(raw, list | tuple):
        logger.warning(
            "%s: discarding non-list payload of type %s; expected a list",
            field_name,
            type(raw).__name__,
        )
        return [], 1
    return list(raw), 0


def _metrics(content: dict[str, object], *, limit: int = 6) -> tuple[list[dict], int]:
    """Scorecard metric cards: keep the dicts, count everything else as lost."""
    # Slot selection is unchanged from before the report existed (``metrics or
    # kpis``, empty falling through), so the counter can never disagree with what
    # was actually rendered — the drift that made finding 2 possible.
    field_name = _first_slot(content, ("metrics", "kpis")) or "metrics"
    raw, dropped = _entries(content.get(field_name), field_name)
    kept = [m for m in raw if isinstance(m, dict)]
    dropped += len(raw) - len(kept)
    if dropped:
        logger.warning(
            "%s: dropped %d of %d malformed entries; rendering the %d valid one(s)",
            field_name,
            dropped,
            len(raw) or dropped,
            len(kept),
        )
    _truncated(field_name, limit, len(kept))
    return kept[:limit], dropped


def _first_slot(content: dict[str, object], keys: tuple[str, ...]) -> str | None:
    """Pick the slot a multi-key family will render from, preserving ``a or b or c``.

    A TRUTHY slot wins first, exactly as the chained ``or`` did — otherwise
    ``items: []`` beside ``bullets: ["a"]`` would newly render nothing. Only when
    none is truthy does a merely-PRESENT slot win, and that case is what makes a
    present-but-destroyed payload report its loss instead of silently falling
    through to a key that is not there either.
    """
    return next(
        (k for k in keys if content.get(k)),
        next((k for k in keys if content.get(k) is not None), None),
    )


def _bullets(content: dict[str, Any], *, limit: int = 6) -> tuple[list[str], int]:
    """Normalise a ``bullets`` slot to a list of non-empty strings.

    Accepts either plain strings or ``{"text": ...}`` dicts (the slot schema
    shape) so the same content works for both the PNG and PPTX renderers.
    Returns ``(bullets, dropped)``.
    """
    return _str_list(content.get("bullets"), limit=limit, field_name="bullets")


def _str_list(raw: Any, *, limit: int = 8, field_name: str = "items") -> tuple[list[str], int]:
    """Normalise an arbitrary list slot to non-empty strings.

    Accepts plain strings or dict items keyed ``text``/``label``/``name`` (the
    shapes the orchestrator's list slots use — agenda ``items``, roadmap
    ``phases``, feature ``bullets``), so the same content renders in either.
    Returns ``(items, dropped)``; an element that normalises to empty text is a
    drop, because the payload asked for a row the slide will not have.
    """
    entries, dropped = _entries(raw, field_name)
    out: list[str] = []
    for item in entries:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("label") or item.get("name") or "")
        elif item is None:
            # ``str(None)`` is the truthy string "None", which used to be painted
            # onto the slide as body copy. ``BaseTemplateRenderer.as_text`` made
            # the same call on the PPTX side: a null is ABSENT, not the word.
            text = ""
        else:
            text = str(item)
        text = text.strip()
        if text:
            out.append(text)
        else:
            dropped += 1
    if dropped:
        logger.warning(
            "%s: dropped %d of %d unrenderable entries; rendering the %d valid one(s)",
            field_name,
            dropped,
            len(entries) or dropped,
            len(out),
        )
    _truncated(field_name, limit, len(out))
    return out[:limit], dropped


def _text_items(content: dict[str, Any], *keys: str, limit: int = 8) -> tuple[list[str], int]:
    """First PRESENT list slot among ``keys``, normalised to strings.

    "Present" is decided on the raw value, not on the normalised result: a slot
    that is present but entirely malformed must report its loss rather than fall
    through to the next key and report nothing.
    """
    key = _first_slot(content, keys)
    if key is None:
        return [], 0
    return _str_list(content.get(key), limit=limit, field_name=key)


def _beats(content: dict[str, Any], *, limit: int = 6) -> tuple[list[dict[str, str]], int]:
    """Normalise a sequential slot to ``{marker, label, description}`` beats.

    Reads the first present of ``beats``/``phases``/``steps``/``bullets`` so the
    timeline family renders roadmap ``phases`` ({name, description, timeline}),
    process_flow ``steps`` ({label, description}), or an authored ``beats`` list
    alike. ``marker`` falls back to a zero-padded ordinal when the source has no
    badge value.
    """
    field_name = _first_slot(content, ("beats", "phases", "steps", "bullets")) or "beats"
    raw, dropped = _entries(content.get(field_name), field_name)
    beats: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        ordinal = f"{index + 1:02d}"
        if isinstance(item, dict):
            marker = str(
                item.get("marker")
                or item.get("timeline")
                or item.get("number")
                or item.get("step_number")
                or ordinal
            ).strip()
            label = str(item.get("label") or item.get("name") or item.get("title") or "").strip()
            description = str(
                item.get("description") or item.get("text") or item.get("detail") or ""
            ).strip()
        else:
            marker = ordinal
            label = str(item).strip()
            description = ""
        if label or description:
            beats.append({"marker": marker or ordinal, "label": label, "description": description})
        else:
            dropped += 1
    if dropped:
        logger.warning(
            "%s: dropped %d of %d unrenderable entries; rendering the %d valid one(s)",
            field_name,
            dropped,
            len(raw) or dropped,
            len(beats),
        )
    _truncated(field_name, limit, len(beats))
    return beats[:limit], dropped


def _column(content: dict[str, Any], side: str) -> tuple[dict[str, Any], int]:
    """Build one ``two_up`` column ({heading, body, bullets}).

    Accepts either a nested ``{side: {heading, body, bullets}}`` dict or flat
    ``{side}_heading`` / ``{side}_body`` / ``{side}_bullets`` keys.
    """
    raw = content.get(side)
    if isinstance(raw, dict):
        heading, body, bullets = raw.get("heading"), raw.get("body"), raw.get("bullets")
    else:
        heading = content.get(f"{side}_heading") or content.get(f"{side}_title")
        body = content.get(f"{side}_body") or content.get(f"{side}_text")
        bullets = content.get(f"{side}_bullets")
    items, dropped = _str_list(bullets, limit=6, field_name=f"{side}_bullets")
    return (
        {
            "heading": str(heading or "").strip(),
            "body": str(body or "").strip(),
            "bullets": items,
        },
        dropped,
    )


def _finish(
    losses: list[SlideContentReport],
    sink: list[SlideContentReport] | None,
) -> SlideContentReport:
    """Merge every slot's outcome into one slide-level report and publish it.

    Module-level rather than inline so ``build_slide_html`` keeps its C901
    budget for the layout-family dispatch, which is the part that has to grow
    when a family is added.
    """
    lost = SlideContentReport()
    for entry in losses:
        lost = lost.merge(entry)
    if sink is not None:
        sink.append(lost)
    return lost


def _record[T](
    losses: list[SlideContentReport],
    field_name: str,
    kept: list[T],
    dropped: int,
) -> list[T]:
    """Append one slot's outcome to *losses* and hand the kept entries straight back.

    Written to be usable inline in a ``ctx.update(...)`` call so that wiring a
    slot into the report is one edit at the site that already normalises it —
    the accounting cannot drift away from the normalisation the way
    ``resolve_slide_images``' slot report once drifted away from its families.
    ``fields`` stays empty when nothing was lost, so a healthy slide merges to
    exactly ``SlideContentReport()``.
    """
    losses.append(
        SlideContentReport(
            offered=len(kept) + dropped,
            dropped=dropped,
            fields=(field_name,) if dropped else (),
        )
    )
    return kept


def build_slide_html(
    *,
    layout_type: str,
    content: dict[str, Any],
    brand: dict[str, Any],
    images: SlideImages | None = None,
    tokens: Mapping[str, str] | None = None,
    content_report: list[SlideContentReport] | None = None,
) -> str:
    """Render the HTML for a slide (pure: no browser, no I/O).

    ``images`` is a pre-resolved {slot_name: image-context} map produced by
    ``render_slide_png`` (data URIs decoded off the event loop). Kept as a
    parameter so this function stays sync + unit-testable without Chromium.

    ``tokens`` is the optional design-system token map (surface / on_surface /
    type_h1 / grid_gutter / ...). Presence is opt-in: ``None`` emits no extra CSS
    custom properties at all and every template falls back to its literal value,
    so the output is byte-identical to the pre-token renderer.

    ``content_report`` is an optional sink list: exactly one
    :class:`SlideContentReport` is appended, so a caller (``render_slide_png`` ->
    ``deck_builder`` -> the manifest) can tell a slide that rendered thin
    *because its entries were malformed* from one that was authored thin. The
    same fact is drawn onto the slide itself, so the PNG the video pipeline
    composites carries it too.
    """
    family = _family(layout_type)
    images = images or {}
    losses: list[SlideContentReport] = []

    display_font, body_font, display_font_uri, body_font_uri = _resolve_fonts(brand)
    ctx: dict[str, Any] = {
        "display_font": display_font,
        "body_font": body_font,
        "display_font_uri": display_font_uri,
        "body_font_uri": body_font_uri,
        "primary": str(brand.get("primary_color") or "#0B1F3A"),
        "secondary": str(brand.get("secondary_color") or "#2BB6A3"),
        "accent": str(brand.get("accent_color") or brand.get("secondary_color") or "#F26B3A"),
        "tokens": _css_tokens(tokens),
    }

    if family == "scorecard":
        metrics, dropped = _metrics(content)
        _record(losses, "metrics", metrics, dropped)
        ctx.update(
            title=_string(content, "title", default="Scorecard"),
            metrics=metrics,
            footer=_string(content, "footer", "trend_description"),
        )
    elif family == "comparison":
        ctx.update(
            title=_string(content, "title", default="Comparison"),
            before_label=_string(content, "before_label", default="Before"),
            after_label=_string(content, "after_label", default="After"),
            caption=_string(content, "caption"),
            before_image=images.get("before_image_url"),
            after_image=images.get("after_image_url"),
        )
    elif family == "image_focus":
        ctx.update(
            title=_string(content, "title", default="Visual summary"),
            description=_string(
                content, "instruction", "description", "subtitle", "recommendation"
            ),
            bullets=_record(losses, "bullets", *_bullets(content)),
            pill=_string(content, "severity", "step_number", "tip"),
            image=images.get("image"),
        )
    elif family == "cta":
        ctx.update(
            eyebrow=_string(content, "eyebrow"),
            title=_string(content, "title", default="Next steps"),
            body=_string(content, "body", "subtitle"),
            cta_text=_string(content, "cta_text"),
            cta_url=_string(content, "cta_url", "url"),
        )
    elif family == "quote":
        ctx.update(
            quote=_string(content, "quote", "text"),
            attribution=_string(content, "attribution", "author", "name"),
            role=_string(content, "role", "source", "company"),
        )
    elif family == "stat":
        ctx.update(
            value=_string(content, "value", "stat", "number", "metric"),
            label=_string(content, "label", "title"),
            caption=_string(content, "caption", "context", "description", "subtitle"),
        )
    elif family == "section_divider":
        ctx.update(
            eyebrow=_string(content, "eyebrow", "chapter", "kicker"),
            title=_string(content, "title", default="Section"),
            items=_record(losses, "items", *_text_items(content, "items", "bullets")),
        )
    elif family == "timeline":
        ctx.update(
            title=_string(content, "title", default="Timeline"),
            beats=_record(losses, "beats", *_beats(content)),
        )
    elif family == "two_up":
        left, left_dropped = _column(content, "left")
        right, right_dropped = _column(content, "right")
        _record(losses, "left-column bullets", left["bullets"], left_dropped)
        _record(losses, "right-column bullets", right["bullets"], right_dropped)
        ctx.update(title=_string(content, "title"), left=left, right=right)
    else:  # title-like (title / default)
        ctx.update(
            eyebrow=_string(content, "eyebrow", default="Slide deck"),
            title=_string(content, "title", default="Untitled slide"),
            subtitle=_string(content, "subtitle", "description"),
        )

    # The marker is drawn on the slide itself, because the PNG is what the video
    # pipeline composites — a manifest counter alone would be invisible in the
    # only artifact a viewer ever sees.
    ctx["dropped_note"] = _finish(losses, content_report).note

    template = _jinja_env().get_template(f"{family}.html.j2")
    return template.render(**ctx)


@dataclass(frozen=True, slots=True)
class SlideImageReport:
    """How many image slots one slide ASKED for, and how many came back empty.

    ``load_image_bytes`` degrades every fetch failure to ``None`` so one dead ref
    cannot abort a deck. That is the right behaviour, but on its own it is
    *indistinguishable* from a slide that never wanted an image: both render
    text-only. This report is the missing signal — ``requested`` counts the slots
    that carried a non-empty ref, ``failed`` the subset that produced no bytes.

    ``requested == 0`` is an intended text slide. ``failed == requested > 0`` is a
    slide whose visual content is simply gone (the common prod case: presigned
    MinIO URLs expire, and every prod ``cut_plan`` run is more than a day old).

    ``unclassified`` is the third state, and it exists so a gap cannot be
    silently resolved in either direction: a key whose NAME says image but whose
    VALUE is not reference-shaped (and is not a registered layout hint) is
    counted HERE, never as a satisfied slot and never as a lost one. Folding it
    into ``requested``/``failed`` is what failed complete decks carrying
    ``image_fit="contain"``; folding it into "nothing was asked for" would hide
    a genuinely missing image.

    ``undrawn`` is the FOURTH state, and it exists for the same reason as the
    third. A slot carrying a perfectly good reference that NO renderer in the
    layout family draws is an authoring defect in the TEMPLATE, not evidence
    about the image: ``load_image_bytes`` is never called on that path, so the
    URL may well be live. Counting it as ``requested``/``failed`` — which is
    what the code did — made ``_guard_image_slots`` answer a question it was
    never asked, and a title slide carrying a brand logo failed its whole job.
    Counting it as nothing would hide a real defect the operator should fix.
    So it is counted HERE: never satisfied, never a loss, always visible.

    The distinction the operator needs is exactly: "your logo URL is dead"
    (``failed``) versus "this template declares a slot nothing draws"
    (``undrawn``). One counter cannot carry both.
    """

    requested: int = 0
    failed: int = 0
    failed_refs: tuple[str, ...] = field(default=())
    unclassified: int = 0
    unclassified_slots: tuple[str, ...] = field(default=())
    undrawn: int = 0
    undrawn_slots: tuple[str, ...] = field(default=())

    def merge(self, other: SlideImageReport) -> SlideImageReport:
        return SlideImageReport(
            requested=self.requested + other.requested,
            failed=self.failed + other.failed,
            failed_refs=self.failed_refs + other.failed_refs,
            unclassified=self.unclassified + other.unclassified,
            unclassified_slots=self.unclassified_slots + other.unclassified_slots,
            undrawn=self.undrawn + other.undrawn,
            undrawn_slots=self.undrawn_slots + other.undrawn_slots,
        )


# The pre-resolved {slot_name: image-context} map ``resolve_slide_images``
# produces and ``build_slide_html`` consumes. Named once so the three signatures
# that pass it around cannot drift, and so the JSON-boundary ``Any`` inside it is
# declared in exactly one place instead of five.
type SlideImages = dict[str, dict[str, Any] | None]


def _slot_report(slot_name: str, ref: str, *, loaded: bool) -> SlideImageReport:
    """Classify one image slot: not asked for / asked and got / asked and lost."""
    if not ref:
        return SlideImageReport()
    if loaded:
        return SlideImageReport(requested=1)
    logger.warning("Image slot %r requested %s but no bytes were loaded", slot_name, ref)
    return SlideImageReport(requested=1, failed=1, failed_refs=(ref,))


def resolve_slide_images(
    layout_type: str, content: dict[str, Any]
) -> tuple[SlideImages, SlideImageReport]:
    """Resolve every image slot a layout needs into a data-URI context (blocking).

    Runs in a worker thread (``load_image_bytes`` does HTTP/disk I/O). Layouts
    without images return an empty map. The second element is the
    :class:`SlideImageReport` that lets a caller tell an imageless deck from an
    intended text deck.

    Structure matters here. Each family branch resolves its own slots AND
    declares the slot NAMES it consumed; the unrenderable-slot sweep then runs on
    the ONE tail every branch returns through. It used to run only in the final
    ``return``, which the two families that actually HAVE images
    (``comparison``, ``image_focus``) never reached — so exactly the payloads
    most likely to carry a stray image key were the ones nobody counted. A
    family added tomorrow gets the sweep whether or not its author remembers it,
    and any slot it forgets to consume is reported rather than silently inert.
    """
    family = _family(layout_type)
    if family == "comparison":
        before_ref = str(content.get("before_image_url") or "").strip()
        after_ref = str(content.get("after_image_url") or "").strip()
        before_bytes = load_image_bytes(before_ref) if before_ref else None
        after_bytes = load_image_bytes(after_ref) if after_ref else None
        return _with_unrenderable_slots(
            family,
            content,
            {
                "before_image_url": _image_slot(
                    content, "before_image_url", image_bytes=before_bytes
                ),
                "after_image_url": _image_slot(content, "after_image_url", image_bytes=after_bytes),
            },
            _slot_report("before_image_url", before_ref, loaded=before_bytes is not None).merge(
                _slot_report("after_image_url", after_ref, loaded=after_bytes is not None)
            ),
            consumed=("before_image_url", "after_image_url"),
        )
    if family == "image_focus":
        slot = "screenshot_url" if content.get("screenshot_url") else "image_url"
        if not content.get(slot) and content.get("preview_image_url"):
            slot = "preview_image_url"
        ref = (
            str(content.get("screenshot_url") or "").strip()
            or str(content.get("image_url") or "").strip()
            or str(content.get("preview_image_url") or "").strip()
        )
        image_bytes = load_image_bytes(ref) if ref else None
        # Exactly ONE slot is drawn, so exactly one is consumed. A second live ref
        # beside it (``preview_image_url`` next to ``screenshot_url``) is an image
        # the caller supplied and the deck does not contain — the tail counts it.
        return _with_unrenderable_slots(
            family,
            content,
            {"image": _image_slot(content, slot, image_bytes=image_bytes)},
            _slot_report(slot, ref, loaded=image_bytes is not None),
            consumed=(slot,),
        )
    return _with_unrenderable_slots(family, content, {}, SlideImageReport(), consumed=())


def _with_unrenderable_slots(
    family: str,
    content: dict[str, object],
    images: SlideImages,
    report: SlideImageReport,
    *,
    consumed: tuple[str, ...],
) -> tuple[SlideImages, SlideImageReport]:
    """The single tail every family returns through: fold in the slots nobody drew."""
    return images, report.merge(_unrenderable_slot_report(family, content, consumed=consumed))


def _unrenderable_slot_report(
    family: str,
    content: dict[str, object],
    *,
    consumed: tuple[str, ...] = (),
) -> SlideImageReport:
    """Count image slots this render had NO ``<img>`` to draw into.

    ``deck_builder._apply_slide_asset_contract`` accepts an image slot on ANY
    layout — it matches purely on the slot NAME (``image_layout.IMAGE_SLOT_TOKENS``) and
    separately ingests every ``asset_urls`` key, with no layout-family check. So
    a caller can legitimately attach ``image_url`` to a ``stat``/``bullets``/
    ``title`` slide, or a ``logo_url`` to a ``comparison`` slide: the URL is
    resolved and written into ``content``, and the family has no slot for it.

    A slot is only counted once ``image_layout.classify_image_slot`` says its
    key is not a LAYOUT HINT (``image_fit``/``image_placement``/... — styling
    values like ``"contain"``, resolved by ``image_layout`` itself) and its value
    has reference SHAPE. Everything else lands in ``unclassified``.

    Returning a bare ``SlideImageReport()`` there reported ``requested == 0``,
    which is byte-identical to a deliberately text-only deck — the gap would be
    inexpressible. So it must be counted; the question is AS WHAT.

    It is counted as ``undrawn``, NOT as ``requested``/``failed``. Counting it
    failed conflated two different facts. "No renderer draws this slot" is a
    statement about the TEMPLATE; "the image failed to load" is a statement
    about the IMAGE — and this function never calls ``load_image_bytes``, so it
    has no evidence whatsoever about the second. Asserting a load failure it
    did not observe is a false claim in the input to a hard-fail gate, and it
    took real decks down: the service's own seeded ``title`` template used to
    declare a ``logo`` slot that neither renderer draws, so a title slide with a
    live brand logo URL failed the entire job.

    ``undrawn`` keeps the defect loud — a WARNING here, a WARNING in
    ``deck_builder._guard_image_slots``, and ``image_slots_undrawn`` on the
    manifest — while leaving the all-images-lost veto to speak only about
    images that were actually fetched and actually lost.

    ``consumed`` names the slots the family DID draw (or at least attempt), and
    they are excluded — they are already accounted for by ``_slot_report``, and
    double-counting a healthy slot would turn a good deck into a failed one.

    ``content`` is typed ``dict[str, object]``, not the looser alias the caller
    holds: every value here comes off a JSON body, and ``object`` forces the
    ``isinstance`` narrowing this function already needs to do before it can
    treat a value as a ref.
    """
    report = SlideImageReport()
    drawn = {str(name).strip().lower() for name in consumed}
    for slot_name, value in content.items():
        slot = str(slot_name or "").strip()
        if not slot or slot.lower() in drawn:
            continue
        kind = classify_image_slot(slot, value)
        if kind is ImageSlotKind.NOT_A_SLOT or kind is ImageSlotKind.LAYOUT_HINT:
            continue
        ref = str(value).strip()
        if kind is ImageSlotKind.UNCLASSIFIED:
            logger.warning(
                "Image slot %r on layout family %r holds %r, which is neither a "
                "reference nor a known layout hint; counting it as unclassified",
                slot,
                family,
                ref,
            )
            report = report.merge(SlideImageReport(unclassified=1, unclassified_slots=(slot,)))
            continue
        logger.warning(
            "Image slot %r on layout family %r carries a ref (%s) but the family has "
            "no image placeholder for it, so nothing was drawn and nothing was "
            "fetched. Counting it as UNDRAWN — this is a template defect (declare "
            "the slot on a family that draws it, or stop declaring it), NOT a failed "
            "image load; the ref itself may be perfectly live.",
            slot,
            family,
            ref,
        )
        report = report.merge(SlideImageReport(undrawn=1, undrawn_slots=(slot,)))
    return report


# Viewport-based CSS sizes cannot fit arbitrary copy. Measure the rendered title
# cards, reduce type only to a readable floor, and fail instead of clipping text.
_FIT_TITLE_TEXT = """() => {
    const block = document.querySelector('.body');
    const slide = document.querySelector('.slide');
    if (!block || !slide) return true;
    const texts = [...block.querySelectorAll('.title,.subtitle,.body-text,.eyebrow,.cta-url')];
    const sizes = texts.map(el => parseFloat(getComputedStyle(el).fontSize));
    const fits = () => {
        const frame = slide.getBoundingClientRect();
        const box = block.getBoundingClientRect();
        return box.top >= frame.top + frame.height * .04 &&
            box.bottom <= frame.bottom - frame.height * .04 &&
            texts.every(el => el.scrollWidth <= el.clientWidth + 1);
    };
    for (let scale = 1; !fits() && scale >= .35; scale -= .05) {
        texts.forEach((el, i) => el.style.fontSize = `${Math.max(24, sizes[i] * scale)}px`);
    }
    return fits();
}"""


async def render_slide_png(
    *,
    layout_type: str,
    content: dict[str, Any],
    brand: dict[str, Any],
    aspect_ratio: str = "16:9",
    scale: float | None = None,
    tokens: Mapping[str, str] | None = None,
    image_report: list[SlideImageReport] | None = None,
    content_report: list[SlideContentReport] | None = None,
) -> bytes:
    """Render a slide preview PNG via headless Chromium (async).

    ``scale`` is the Chromium device pixel ratio. It leaves the 1920x1080 CSS
    layout untouched (so wrapping/balance stay identical) while emitting a
    higher-resolution PNG — the lever for 2K/4K output without re-flowing the
    slide templates. ``None`` falls back to the ``RENDER_DEVICE_SCALE_FACTOR``
    env default; an explicit per-request value wins.

    ``tokens`` is the optional design-system token map (see ``build_slide_html``);
    ``None`` renders exactly as before.

    ``image_report`` is an optional sink list: exactly one
    :class:`SlideImageReport` is appended to it, so the caller can tell a slide
    that rendered text-only *because its image failed* from one that never had an
    image. Rendering itself is unchanged — a failed image is still a placeholder,
    never an exception, so one dead ref cannot abort a deck.

    ``content_report`` is the same shape for list content: exactly one
    :class:`SlideContentReport` is appended, so the caller can tell a slide that
    rendered thin because its entries were malformed from one authored thin.
    """
    width, height = _VIEWPORT_BY_RATIO.get(
        str(aspect_ratio or "16:9").strip(), _VIEWPORT_BY_RATIO["16:9"]
    )
    images, report = await asyncio.to_thread(resolve_slide_images, layout_type, content)
    if image_report is not None:
        image_report.append(report)
    html = build_slide_html(
        layout_type=layout_type,
        content=content,
        brand=brand,
        images=images,
        tokens=tokens,
        content_report=content_report,
    )

    from src.config import get_settings

    settings = get_settings()
    timeout_ms = settings.render.page_timeout_ms
    # Clamp to Playwright's valid range (>=1); fall back to the env default.
    effective_scale = settings.render.device_scale_factor if scale is None else scale
    effective_scale = max(1.0, min(4.0, float(effective_scale)))

    # The HTML is fully self-contained (font + images are inline data URIs), so
    # set_content needs no temp file and triggers no network — simpler than
    # html_render's file:// goto and avoids any filesystem write at render time.
    pool = await get_browser_pool()
    async with pool.lease() as browser:
        page = await browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=effective_scale,
        )
        try:
            await page.set_content(html, wait_until="load", timeout=timeout_ms)
            # Embedded @font-face is applied asynchronously; wait so text
            # metrics (and thus wrapping) are deterministic before capture.
            #
            # ``page.evaluate`` takes NO timeout of its own and is not covered by
            # ``page_timeout_ms`` (that argument belongs to ``set_content``), so a
            # promise that never settles used to hang here forever — holding the
            # pool slot for the life of the pod. Bound it explicitly and shoot the
            # slide anyway: slightly-off font metrics beat a wedged pool.
            try:
                await asyncio.wait_for(
                    page.evaluate("document.fonts.ready"), timeout=timeout_ms / 1000
                )
            except TimeoutError:
                logger.warning(
                    "document.fonts.ready did not settle within %dms; "
                    "capturing anyway (text metrics may be off)",
                    timeout_ms,
                )
            if _family(layout_type) in {"title", "cta"}:
                fits = await asyncio.wait_for(
                    page.evaluate(_FIT_TITLE_TEXT), timeout=timeout_ms / 1000
                )
                if fits is False:
                    raise ValueError(
                        "Slide text exceeds the readable title-card area; shorten the display copy or split the slide"
                    )
            return await page.screenshot(full_page=False)
        finally:
            await page.close()
