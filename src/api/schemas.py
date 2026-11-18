"""Pydantic request/response models for slide_service API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------


class BrandConfig(BaseModel):
    """Brand configuration for deck generation."""

    primary_color: str = Field(default="#1a73e8", description="Primary brand color (hex)")
    secondary_color: str = Field(default="#34a853", description="Secondary brand color (hex)")
    font_family: str = Field(default="Arial", description="Primary font family")
    logo_url: str | None = Field(default=None, description="URL to brand logo")
    theme: str = Field(default="modern", description="Theme name")


# ---------------------------------------------------------------------------
# Content Shaper
# ---------------------------------------------------------------------------


class ContentShaperConfig(BaseModel):
    """Configuration for LLM content shaping."""

    enabled: bool = Field(default=False, description="Enable LLM content shaping")
    summarize: bool = Field(default=True, description="Summarize verbose content")
    generate_speaker_notes: bool = Field(default=True, description="Generate speaker notes")
    generate_transitions: bool = Field(default=False, description="Generate transition text")
    tone: str = Field(default="professional", description="Content tone")


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


class TimingConfig(BaseModel):
    """Timing configuration for deck generation."""

    target_duration_seconds: float = Field(default=120.0, description="Target total deck duration")
    default_slide_duration_seconds: float = Field(
        default=15.0, description="Default per-slide duration"
    )


# ---------------------------------------------------------------------------
# Slide Content
# ---------------------------------------------------------------------------


class AssetPlacement(BaseModel):
    """Presentation hints for rendering a slide image slot."""

    mode: Literal["framed", "full_bleed"] = Field(
        default="framed",
        description="Render inside the template frame or as a full-bleed background",
    )
    fit: Literal["cover", "contain"] = Field(
        default="cover",
        description="Crop to cover the slot or contain the full image inside the slot",
    )


class SlideContent(BaseModel):
    """Content for a single slide in a deck request."""

    template_id: str = Field(..., description="UUID of the template to use")
    role: str | None = Field(default=None, description="Semantic role for downstream mapping")
    content: dict = Field(default_factory=dict, description="Slot content key-value pairs")
    asset_urls: dict[str, str] = Field(
        default_factory=dict,
        description="Optional named asset URLs or asset references keyed by content slot name",
    )
    asset_placements: dict[str, AssetPlacement] = Field(
        default_factory=dict,
        description="Per-slot image placement hints keyed by content slot name",
    )


class MetricItem(BaseModel):
    """A single metric for scorecard/metric_summary templates."""

    label: str
    value: str


class DeckAssetInput(BaseModel):
    """Optional image/document asset included with the deck request."""

    asset_id: str = Field(..., description="Stable asset identifier")
    label: str = Field(default="", description="Human-friendly label")
    kind: str = Field(default="image", description="Asset kind: current, redesign, app, doc, image")
    role: str | None = Field(default=None, description="Optional semantic role")
    image_url: str = Field(..., description="Image reference or data URL")
    metadata: dict = Field(default_factory=dict, description="Additional asset metadata")


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------


class TemplateCreateRequest(BaseModel):
    """Request to create a custom template."""

    name: str = Field(..., max_length=100, description="Machine name")
    display_name: str = Field(..., max_length=200, description="Human label")
    category: str = Field(..., max_length=50, description="Grouping category")
    layout_type: str = Field(..., max_length=50, description="Layout identifier")
    slot_definitions: dict = Field(..., description="Typed slot schema")
    styling_rules: dict = Field(default_factory=dict, description="Visual configuration")
    default_transition: dict | None = Field(default=None, description="Default transition config")
    default_timing_hint_seconds: float = Field(
        default=10.0, gt=0, description="Suggested slide duration (seconds, must be > 0)"
    )


class TemplateUpdateRequest(BaseModel):
    """Request to update a template."""

    display_name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=50)
    slot_definitions: dict | None = None
    styling_rules: dict | None = None
    default_transition: dict | None = None
    default_timing_hint_seconds: float | None = Field(default=None, gt=0)


class TemplateResponse(BaseModel):
    """Response for a single template."""

    id: str
    name: str
    display_name: str
    category: str
    layout_type: str
    slot_definitions: dict
    styling_rules: dict
    default_transition: dict | None = None
    default_timing_hint_seconds: float | None = None
    is_system: bool = False
    is_active: bool = True


class TemplateListResponse(BaseModel):
    """Paginated template list response."""

    templates: list[TemplateResponse]
    total: int
    page: int = 1
    page_size: int = 50


class TemplateVersionResponse(BaseModel):
    """Response for a template version."""

    version_id: str
    template_id: str
    version_number: int
    snapshot_hash: str
    slot_definitions: dict
    styling_rules: dict
    default_transition: dict | None = None
    default_timing_hint_seconds: float | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Deck Generation
# ---------------------------------------------------------------------------


class DeckCreateRequest(BaseModel):
    """Request to create a deck generation job."""

    title: str = Field(..., description="Deck title")
    template_ids: list[str] = Field(
        default_factory=list, description="Template IDs used in this deck"
    )
    slides: list[SlideContent] = Field(
        ...,
        min_length=1,
        description=(
            "Slide content list. At least one slide is required: an empty list "
            "produced an empty PPTX/PDF and a zero-asset manifest that still "
            "reported the job 'completed'."
        ),
    )
    image_assets: list[DeckAssetInput] = Field(
        default_factory=list,
        description="Optional image assets referenced by the deck",
    )
    brand: BrandConfig = Field(default_factory=BrandConfig)
    tokens: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional design-system tokens (e.g. surface, surface_2, on_surface, "
            "on_surface_muted, accent_2, accent_soft, type_display, type_h1, type_body, "
            "grid_gutter, grid_margin). Each entry is emitted as a CSS custom property "
            "(`on_surface` -> `--on-surface`) on the rendered slide and overrides the "
            "template's built-in value. Presence is opt-in: omitting it (or null) renders "
            "exactly as before."
        ),
    )
    aspect_ratio: str = Field(default="16:9", description="Slide aspect ratio")
    render_scale: float | None = Field(
        default=None,
        ge=1.0,
        le=4.0,
        description=(
            "Chromium device pixel ratio for slide PNG capture. None = the "
            "service default (RENDER_DEVICE_SCALE_FACTOR). 1.333 -> 2K, 2.0 -> 4K. "
            "Keeps the 1920x1080 layout; only raises output resolution/sharpness."
        ),
    )
    content_shaper: ContentShaperConfig = Field(default_factory=ContentShaperConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)


class DeckJobResponse(BaseModel):
    """Response after creating a deck job (202)."""

    job_id: str
    status: str


class DeckStatusResponse(BaseModel):
    """Response when polling deck job status."""

    job_id: str
    status: str
    slide_count: int | None = None
    created_at: str | None = None
    error: str | None = None


class DeckManifestResponse(BaseModel):
    """Manifest JSON response."""

    manifest_version: str = "1.0"
    deck_id: str
    title: str
    aspect_ratio: str = "16:9"
    slide_count: int = 0
    total_estimated_duration_seconds: float = 0.0
    brand: dict = Field(default_factory=dict)
    assets: dict = Field(default_factory=dict)
    slides: list[dict] = Field(default_factory=list)
    image_slots_requested: int = Field(
        default=0,
        description=(
            "Image slots this deck asked for across all slides. 0 means the deck "
            "was deliberately text-only."
        ),
    )
    image_slots_failed: int = Field(
        default=0,
        description=(
            "Image slots that were requested but produced no bytes (expired/403 "
            "presigned URL, SSRF rejection, timeout). Non-zero means the deck is "
            "missing imagery it asked for; the deck build hard-fails when ALL of "
            "them failed."
        ),
    )
    image_slots_unclassified: int = Field(
        default=0,
        description=(
            "Slots whose key NAME looks like an image but whose value is neither a "
            "reference nor a registered layout hint (image_fit/image_placement/...). "
            "Counted separately on purpose: they are not satisfied slots and not "
            "failed ones, so neither counter may absorb them. Non-zero means a key "
            "needs registering in image_layout.IMAGE_LAYOUT_HINT_KEYS or a real ref."
        ),
    )
    image_slots_undrawn: int = Field(
        default=0,
        description=(
            "Slots that carried a real image reference which no renderer in their "
            "layout family draws (e.g. a logo attached to a title slide). Counted "
            "apart from image_slots_failed on purpose: nothing was fetched, so this "
            "is NOT evidence the image is dead — it is a TEMPLATE defect. Non-zero "
            "means a slot is declared where nothing can render it; it never fails "
            "the deck."
        ),
    )
    content_entries_offered: int = Field(
        default=0,
        description=(
            "List entries (metrics, bullets, items, timeline beats, column "
            "bullets) this deck's payload offered across all slides."
        ),
    )
    content_entries_dropped: int = Field(
        default=0,
        description=(
            "Offered entries that were unrenderable (non-dict metric, non-list "
            "slot, entry that normalised to empty text) and so are absent from "
            "the slides. Non-zero means a PNG the video pipeline composites is "
            "thinner than the payload asked for; each affected slide also carries "
            "a visible degraded marker. 0 with a non-zero offered count is a "
            "healthy deck, and 0/0 is a deliberately text-only one."
        ),
    )
    shaping_slides_requested: int = Field(
        default=0,
        description=(
            "Slides submitted to the LLM content shaper. 0 means shaping was not "
            "enabled for this deck, which is a legitimate, non-degraded outcome."
        ),
    )
    shaping_slides_shaped: int = Field(
        default=0,
        description=(
            "Submitted slides that actually took at least one shaped value "
            "(summarised content, speaker notes, or transition text)."
        ),
    )
    shaping_slides_unshaped: int = Field(
        default=0,
        description=(
            "Submitted slides that came back untouched and therefore ship RAW — "
            "unsummarised, with no generated speaker notes. This is the count of "
            "affected slides; the shaper fails open on purpose, so the deck is "
            "still delivered and the job still reports success."
        ),
    )
    shaping_degraded_code: str = Field(
        default="",
        description=(
            "Stable machine-readable degradation code. Empty string means shaping "
            "was healthy OR was never requested. The only non-empty value is "
            "'shaping_failed', which means the shaper was asked for and did not "
            "fully deliver: the deck ships partly or wholly unshaped while the "
            "worker_management call was still billed. Branch on this instead of "
            "grepping the service's ERROR log."
        ),
    )
    shaping_degraded_reason: str = Field(
        default="",
        description=(
            "Where shaping broke, when shaping_degraded_code is set: "
            "'prompt_render_failed' (the prompt template would not render, a code "
            "defect, no WM call was made), 'wm_call_failed' (WM 4xx/5xx, timeout "
            "or connection failure), 'no_usable_output' (WM answered with a shape "
            "carrying no slides), 'partial_merge' (the model answered for only "
            "some slides, or every value it returned for a slide was the wrong "
            "type). Empty when the code is empty."
        ),
    )
