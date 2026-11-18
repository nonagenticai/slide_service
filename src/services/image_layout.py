"""Shared helpers for deck image placement semantics.

This module is also the AUTHORITY on what an image-ish ``content`` key MEANS.
Two disjoint families of keys carry the substring ``image``:

* an image **reference** — ``image_url``, ``screenshot_url``, ``hero_image`` —
  whose value points at bytes to draw;
* an image **layout hint** — ``image_fit``, ``image_placement`` — whose value is
  a rendering semantic (``"contain"``, ``"full_bleed"``) consumed *here*.

Anything that classifies keys by name must import :data:`IMAGE_LAYOUT_HINT_KEYS`
from this module rather than re-listing the names, because this module is where
the hint keys are actually READ: the resolvers below iterate the very same
tuples, so a hint key cannot be consumed here without also being excluded from
reference counting. A hand-maintained blacklist elsewhere would drift the first
time a fifth hint key is added — which is exactly the bug this exists to make
impossible.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Final

logger = logging.getLogger(__name__)

IMAGE_PLACEMENT_FRAME = "image_frame"
IMAGE_PLACEMENT_FULL_BLEED = "full_bleed"
IMAGE_FIT_COVER = "cover"
IMAGE_FIT_CONTAIN = "contain"

_IMAGE_PLACEMENT_ALIASES = {
    "frame": IMAGE_PLACEMENT_FRAME,
    "image_frame": IMAGE_PLACEMENT_FRAME,
    "framed": IMAGE_PLACEMENT_FRAME,
    "panel": IMAGE_PLACEMENT_FRAME,
    "card": IMAGE_PLACEMENT_FRAME,
    "full_bleed": IMAGE_PLACEMENT_FULL_BLEED,
    "bleed": IMAGE_PLACEMENT_FULL_BLEED,
    "edge_to_edge": IMAGE_PLACEMENT_FULL_BLEED,
    "edge-to-edge": IMAGE_PLACEMENT_FULL_BLEED,
}

_IMAGE_FIT_ALIASES = {
    "cover": IMAGE_FIT_COVER,
    "crop": IMAGE_FIT_COVER,
    "fill": IMAGE_FIT_COVER,
    "contain": IMAGE_FIT_CONTAIN,
    "fit": IMAGE_FIT_CONTAIN,
    "fit_inside": IMAGE_FIT_CONTAIN,
    "fit-inside": IMAGE_FIT_CONTAIN,
    "inside": IMAGE_FIT_CONTAIN,
}


# Content/styling keys read by ``resolve_image_placement`` — in priority order.
IMAGE_PLACEMENT_HINT_KEYS: Final[tuple[str, ...]] = (
    "image_placement",
    "image_style",
    "visual_treatment",
)

# Content/styling keys read by ``resolve_image_fit`` — in priority order.
IMAGE_FIT_HINT_KEYS: Final[tuple[str, ...]] = ("image_fit", "image_scale")

# Per-slot placement contract read by ``resolve_asset_layout``. Its value is a
# mapping, never a ref, but it is listed for completeness of "keys this module
# consumes".
IMAGE_SLOT_LAYOUT_KEY: Final[str] = "asset_placements"

#: Every ``content``/``styling_rules`` key this module consumes as a LAYOUT HINT.
#: Derived from the tuples the resolvers iterate, so it cannot drift from them.
IMAGE_LAYOUT_HINT_KEYS: Final[frozenset[str]] = frozenset(
    IMAGE_PLACEMENT_HINT_KEYS + IMAGE_FIT_HINT_KEYS + (IMAGE_SLOT_LAYOUT_KEY,)
)

# Substrings that mark a ``content`` key as an image slot. Single source of
# truth: ``deck_builder._apply_slide_asset_contract`` resolves ``asset://`` refs
# for exactly these keys, and ``html_slide_renderer.resolve_slide_images`` counts
# exactly these keys on the families that have nowhere to draw them. If the two
# lists drifted, a slot could be resolved-and-written but never counted.
IMAGE_SLOT_TOKENS: Final[tuple[str, ...]] = (
    "image",
    "screenshot",
    "logo",
    "preview",
    "background",
    "illustration",
)

_IMAGE_REFERENCE_SCHEMES: Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "data:",
    "asset://",
    "asset:",
    "file://",
    "s3://",
    "minio://",
)

#: Key SUFFIXES that mark an image-ish key as textual metadata ABOUT an image
#: rather than a pointer to one. ``image_caption``/``image_credit``/``image_alt``
#: all contain an :data:`IMAGE_SLOT_TOKENS` substring, so the token match alone
#: promotes prose into the reference pipeline; the ``/`` in ``"Before/After
#: growth"`` then gave it reference SHAPE and it was counted as a lost image.
#:
#: This is a NAME-based rule and is checked before any value sniffing, because
#: the name is the reliable signal here: a key called ``*_caption`` is a caption
#: whatever its value looks like. It is deliberately a suffix match on the whole
#: key — ``image_url``, ``hero_image``, ``logo``, ``screenshot_url`` and
#: ``background_image`` end in none of these, so no real reference slot is
#: demoted. A slot that genuinely holds a ref must not be named after prose.
IMAGE_DESCRIPTIVE_SUFFIXES: Final[tuple[str, ...]] = (
    "_caption",
    "_credit",
    "_alt",
    "_alt_text",
    "_description",
    "_label",
    "_title",
    "_prompt",
    "_query",
    "_note",
    "_notes",
)

_IMAGE_REFERENCE_SUFFIXES: Final[tuple[str, ...]] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".avif",
    ".tif",
    ".tiff",
)


class ImageSlotKind(StrEnum):
    """What an image-ish ``content`` key actually is.

    The fourth member is the point: a key whose NAME says image but whose VALUE
    has no reference shape is neither a satisfied slot nor a lost one. Counting
    it as a loss fails a deck that is complete (the ``image_fit="contain"``
    bug); counting it as satisfied hides a real missing image. It gets its own
    state so the gap stays expressible and loud.
    """

    NOT_A_SLOT = "not_a_slot"
    LAYOUT_HINT = "layout_hint"
    REFERENCE = "reference"
    UNCLASSIFIED = "unclassified"


def looks_like_image_reference(value: str) -> bool:
    """Does this VALUE have the shape of something that points at bytes?

    Only POSITIVE signals decide: a URI scheme, a path separator, or an image
    file extension. A layout hint carries none of them — ``"contain"``,
    ``"full_bleed"`` and every alias in the tables above are bare words. Shape
    is checked in ADDITION to the key name so that a hint key nobody has
    registered here yet still cannot be mistaken for a lost image.

    ⚠️ Whitespace is deliberately NOT a blanket disqualifier. Object keys and
    filenames legitimately contain spaces (``decks/job 1/a.png``), and a
    presigned URL can too. An earlier revision rejected any value containing
    whitespace BEFORE testing the positive signals, which quietly demoted a
    real, unloadable reference to ``UNCLASSIFIED`` — it escaped the all-failed
    veto and the deck shipped missing its imagery. That is the exact fail-open
    shape this check exists to remove.

    ⚠️ A bare ``"/" in text`` was too loose in the OTHER direction: ordinary
    prose uses slashes (``"Before/After growth"``,
    ``"Photo: Unsplash / Jane"``), so any sentence with a slash acquired
    reference shape. The separator rule is therefore narrowed to values that
    also look like a PATH rather than a sentence — no interior whitespace — and
    the extension rule is widened to strip a query/fragment first so that a
    presigned key (``w2v/hero.png?X-Amz-Signature=...``) still matches on its
    real extension.

    Losing a true positive is the worse error of the two, so the extension test
    keeps its own path: ``decks/job 1/a.png`` has a space AND a slash and is
    still a reference, because its final path segment ends in ``.png``. What is
    given up is only the *extension-less, whitespace-carrying* relative path —
    a shape no producer in this service emits (every writer of an image slot
    goes through ``_resolve_asset_reference``, which yields a scheme'd URL or a
    raw catalog id), and one that lands in ``UNCLASSIFIED``, i.e. logged and
    counted, never silently satisfied.
    """
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(_IMAGE_REFERENCE_SCHEMES):
        return True
    # Strip a query string / fragment before the extension test: the extension is
    # a property of the PATH, and a presigned URL carries its signature after it.
    path = lowered.split("?", 1)[0].split("#", 1)[0].rstrip()
    if path.endswith(_IMAGE_REFERENCE_SUFFIXES):
        return True
    separated = "/" in text or "\\" in text
    return separated and not any(char.isspace() for char in text)


def classify_image_slot(slot_name: str, value: object) -> ImageSlotKind:
    """Classify one ``content`` entry as reference / layout hint / neither.

    Ordering is deliberate: the layout-hint check runs FIRST and on the exact
    key, because this module's own resolvers are the authority for those keys.
    Only a key that survives that check is matched against
    :data:`IMAGE_SLOT_TOKENS`, and only then is its value shape consulted.

    KNOWN, DELIBERATE EDGE: a bare unresolvable asset id (``image_url:
    "hero-1"``) is shape-identical to a styling word and lands in
    ``UNCLASSIFIED``, where the pre-fix code counted it failed. It is not
    promoted to a reference, because the only rule that would do so — "an
    unregistered bare word under an image-ish key is a lost image" — is the
    substring guess that failed complete decks in the first place, and it would
    fire on the next unregistered styling key.

    REACHABILITY — the ordinary catalog-miss route is enough; no bypass of the
    asset contract is required. ``deck_builder._resolve_asset_reference`` falls
    back to the RAW string when the catalog has no entry, and the ``asset_urls``
    loop in ``_apply_slide_asset_contract`` writes that fallback straight into
    ``content[slot]``. So ``asset_urls: {"image_url": "hero-1"}`` with no
    matching ``image_assets`` entry reaches the renderer as a bare id and lands
    here. (``asset://hero-1`` is reference-shaped and, on a catalog HIT, both
    spellings arrive as a real URL — but a MISS is not an exotic path.)

    BEHAVIOUR CHANGE, ACCEPTED DELIBERATELY: pre-fix, such a deck was VETOED —
    the id counted ``requested=1, failed=1`` and the all-failed guard raised.
    Here it WARNS AND SHIPS. That is a narrowing, chosen because the alternative
    reintroduces the false-failure class; it is not silent — two WARNINGs fire
    (this function and ``deck_builder._guard_image_slots``) and the count is on
    the manifest as ``image_slots_unclassified``. Pinned by
    test_a_bare_unresolvable_asset_id_is_unclassified.
    """
    slot = str(slot_name or "").strip().lower()
    if not slot:
        return ImageSlotKind.NOT_A_SLOT
    if slot in IMAGE_LAYOUT_HINT_KEYS:
        return ImageSlotKind.LAYOUT_HINT
    if not any(token in slot for token in IMAGE_SLOT_TOKENS):
        return ImageSlotKind.NOT_A_SLOT
    # A key NAMED after prose about an image is never a pointer to one. Checked
    # on the name, before any value sniffing, because the name is the reliable
    # signal: ``image_caption`` is a caption whatever its value happens to look
    # like. See IMAGE_DESCRIPTIVE_SUFFIXES for why no real ref slot is demoted.
    if slot.endswith(IMAGE_DESCRIPTIVE_SUFFIXES):
        return ImageSlotKind.NOT_A_SLOT
    if not isinstance(value, str) or not value.strip():
        return ImageSlotKind.NOT_A_SLOT
    if looks_like_image_reference(value):
        return ImageSlotKind.REFERENCE
    logger.warning(
        "Image slot %r carries value %r, which has no reference shape and is not a "
        "known layout hint (%s) — counting it as UNCLASSIFIED, neither satisfied "
        "nor failed",
        slot_name,
        value,
        ", ".join(sorted(IMAGE_LAYOUT_HINT_KEYS)),
    )
    return ImageSlotKind.UNCLASSIFIED


def normalize_image_placement(value: Any, default: str = IMAGE_PLACEMENT_FRAME) -> str:
    """Normalize placement aliases to a stable rendering semantic."""
    normalized = str(value or "").strip().lower().replace(" ", "_")
    if not normalized:
        return default
    return _IMAGE_PLACEMENT_ALIASES.get(normalized, default)


def normalize_image_fit(value: Any, default: str = IMAGE_FIT_COVER) -> str:
    """Normalize image fit aliases to a stable rendering semantic."""
    normalized = str(value or "").strip().lower().replace(" ", "_")
    if not normalized:
        return default
    return _IMAGE_FIT_ALIASES.get(normalized, default)


def resolve_image_placement(
    content: dict[str, Any] | None,
    styling_rules: dict[str, Any] | None,
    *,
    default: str = IMAGE_PLACEMENT_FRAME,
) -> str:
    """Resolve image placement from content first, then styling rules."""
    candidates = [(content or {}).get(key) for key in IMAGE_PLACEMENT_HINT_KEYS] + [
        (styling_rules or {}).get(key) for key in IMAGE_PLACEMENT_HINT_KEYS
    ]
    for candidate in candidates:
        normalized = normalize_image_placement(candidate, default="")
        if normalized:
            return normalized
    return default


def resolve_image_fit(
    content: dict[str, Any] | None,
    styling_rules: dict[str, Any] | None,
    *,
    default: str = IMAGE_FIT_COVER,
) -> str:
    """Resolve fit behavior from content first, then styling rules."""
    candidates = [(content or {}).get(key) for key in IMAGE_FIT_HINT_KEYS] + [
        (styling_rules or {}).get(key) for key in IMAGE_FIT_HINT_KEYS
    ]
    for candidate in candidates:
        normalized = normalize_image_fit(candidate, default="")
        if normalized:
            return normalized
    return default


def resolve_asset_layout(
    content: dict[str, Any] | None,
    styling_rules: dict[str, Any] | None,
    *,
    slot_name: str,
    default_placement: str = IMAGE_PLACEMENT_FRAME,
    default_fit: str = IMAGE_FIT_COVER,
) -> tuple[str, str]:
    """Resolve placement and fit for a specific image slot.

    `asset_placements[slot_name]` is the explicit contract. Legacy top-level
    `image_placement` and `image_fit` fields remain as fallback.
    """
    slot_layout = None
    asset_placements = (content or {}).get(IMAGE_SLOT_LAYOUT_KEY)
    if isinstance(asset_placements, dict):
        candidate = asset_placements.get(slot_name)
        if isinstance(candidate, dict):
            slot_layout = candidate

    placement = normalize_image_placement(
        (slot_layout or {}).get("mode"),
        default="",
    ) or resolve_image_placement(content, styling_rules, default=default_placement)
    fit = normalize_image_fit(
        (slot_layout or {}).get("fit"),
        default="",
    ) or resolve_image_fit(content, styling_rules, default=default_fit)
    return placement, fit
