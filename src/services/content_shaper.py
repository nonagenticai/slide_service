"""LLM content polishing for slide decks via WMClient (worker_management).

w2v services route every LLM call through worker_management — never call
openai / anthropic / inference-router directly (memory:
``wm-as-sole-llm-gateway.md``). WM handles provider selection, license-token
metering, and structured output enforcement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from w2v_common.clients import WMClient

from src.config import Settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "content_shaper"

_WORKER_NAME = "slide-bullet-extractor"

# Stable, machine-readable degradation code. The orchestrator branches on this
# string; it is API surface, so it must not be reworded.
SHAPING_FAILED = "shaping_failed"

# Stable reason codes. ``code`` says WHETHER the deck is degraded; ``reason``
# says WHERE it broke, so an operator does not have to go find the ERROR log
# line that used to be the only evidence.
REASON_PROMPT_RENDER_FAILED = "prompt_render_failed"
REASON_WM_CALL_FAILED = "wm_call_failed"
REASON_NO_USABLE_OUTPUT = "no_usable_output"
REASON_PARTIAL_MERGE = "partial_merge"


@dataclass(frozen=True, slots=True)
class ShapingReport:
    """What shaping was asked for, what it delivered, and how it degraded.

    The PNG path's :class:`~src.services.html_slide_renderer.SlideContentReport`
    twin, for the LLM stage. ``shape_content`` is documented as failing OPEN — a
    transient WM hiccup returns the raw slides rather than losing a paid deck —
    and that is correct and stays. The defect this closes is that the degradation
    reached nobody: the job reported success and the only evidence was an ERROR
    log line, so a caller could not tell an unshaped deck from a polished one
    without grepping logs.

    ``code`` is ``""`` for every healthy outcome, including shaping being
    disabled: a text-only, unshaped-by-request deck is legitimate and must stay
    indistinguishable from one that was never asked to shape. ``code`` is only
    ever :data:`SHAPING_FAILED`, so a consumer can branch on truthiness.

    ``unshaped`` is the count of slides that were submitted and came back
    untouched — the "count of affected slides" a caller needs to decide whether
    to re-run. It is NOT an error count: a partial merge (the model answered for
    3 of 5 slides) is a real, reportable degradation of exactly 2.
    """

    requested: int = 0
    shaped: int = 0
    unshaped: int = 0
    code: str = ""
    reason: str = ""


def _publish(
    sink: list[ShapingReport] | None,
    report: ShapingReport,
) -> ShapingReport:
    """Append *report* to the caller's sink, if it supplied one.

    Sink-list convention, not a return-type change, because it is the one this
    service already uses for exactly this job (``render_slide_png``'s
    ``image_report=`` / ``content_report=``) and because ``shape_content``'s
    ``list[dict]`` return is consumed positionally by callers and tests.
    """
    if sink is not None:
        sink.append(report)
    return report


# Schema is object-rooted (WM/structured-output convention) — the parsed
# slide list lives under ``slides``. Each shaped slide echoes its 0-based
# index plus the polished content/speaker_notes/transition_text.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "0-based slide index (must be >= 0 and < number of slides)",
                    },
                    "content": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Polished slot content keyed by slot name",
                    },
                    "speaker_notes": {"type": "string"},
                    "transition_text": {"type": "string"},
                },
                "required": ["index", "content"],
            },
        }
    },
    "required": ["slides"],
}


def _load_prompt_template() -> str:
    """Load the Jinja2 prompt template for content shaping."""
    template_path = _TEMPLATE_DIR / "deck_shaping.md"
    if template_path.exists():
        return template_path.read_text()
    return _DEFAULT_PROMPT


_DEFAULT_PROMPT = """Polish the slide content below to fit the brand, tone,
and slot constraints provided.

## Brand
- Primary color: {{ brand.primary_color }}
- Font: {{ brand.font_family }}
- Theme: {{ brand.theme }}

## Tone: {{ tone }}

## Slides
{% for slide in slides %}
### Slide {{ loop.index0 }}: {{ slide.template_name }}
Content: {{ slide.content | tojson }}
Slot definitions: {{ slide.slot_definitions | tojson }}
{% endfor %}

## Instructions
{% if summarize %}Summarize verbose content to fit slot max_length constraints.{% endif %}
{% if generate_speaker_notes %}Generate concise speaker notes for each slide.{% endif %}
{% if generate_transitions %}Generate natural transition text between slides.{% endif %}

Return an object ``{"slides": [...]}`` where each item has:
- "index": 0-based slide index
- "content": polished content dict matching slot definitions
- "speaker_notes": string (if requested)
- "transition_text": string (if requested)"""


class ContentShaper:
    """Optional LLM-powered content polishing for deck slides."""

    def __init__(self, settings: Settings, wm_client: WMClient | None = None) -> None:
        # One shared WMClient per ContentShaper instance — its underlying
        # httpx.AsyncClient is connection-pooled and the identity event hook
        # reads request-scoped ContextVars, so the same instance serves
        # concurrent end-user requests with correct tenant attribution.
        self._settings = settings
        self._wm = wm_client if wm_client is not None else WMClient()

    async def shape_content(
        self,
        slides: list[dict[str, Any]],
        brand: dict[str, Any],
        shaper_config: dict[str, Any],
        *,
        report: list[ShapingReport] | None = None,
    ) -> list[dict[str, Any]]:
        """Polish slide content via WM. Returns the merged slides on success,
        or the original slides on any prompt-render / WM / parse failure
        (fail-open).

        Exactly one :class:`ShapingReport` is appended to *report* on EVERY
        return path, so an absent report means this method was never called —
        never that it succeeded.
        """
        if not shaper_config.get("enabled", False):
            # Not asked for. Not a degradation: `requested=0`, empty code.
            _publish(report, ShapingReport())
            return slides

        total = len(slides)
        prompt = _render_shaping_prompt(slides, brand, shaper_config)
        if prompt is None:
            # The prompt failed to render, so it carries ZERO slide content.
            # Sending the raw template text to WM would still "succeed" and the
            # model would invent a whole deck out of nothing, which we would then
            # merge and ship to the tenant. Degrade exactly like the WM-failure
            # path below: skip shaping, return the deck untouched.
            logger.error(
                "Content shaping prompt failed to render; skipping shaping for %d slides",
                len(slides),
            )
            _publish(
                report,
                ShapingReport(
                    requested=total,
                    shaped=0,
                    unshaped=total,
                    code=SHAPING_FAILED,
                    reason=REASON_PROMPT_RENDER_FAILED,
                ),
            )
            return slides

        try:
            response = await self._wm.message(
                worker_name=_WORKER_NAME,
                message=prompt,
                output_schema=_OUTPUT_SCHEMA,
            )
        except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
            # WMClient.message raises httpx.HTTPStatusError / httpx.RequestError
            # (both httpx.HTTPError subclasses, NOT OSError) on a WM 4xx/5xx,
            # timeout or connection failure -- the most common WM failure mode.
            # Keep the documented fail-open: a shaping hiccup must degrade to
            # the original slides, never fail the whole deck.
            logger.error("Content shaping WM call failed: %s", exc)
            _publish(
                report,
                ShapingReport(
                    requested=total,
                    shaped=0,
                    unshaped=total,
                    code=SHAPING_FAILED,
                    reason=REASON_WM_CALL_FAILED,
                ),
            )
            return slides

        shaped = _extract_shaped_slides(response.output)
        if not shaped:
            # The WM call was MADE and BILLED, and nothing usable came back:
            # ``_extract_shaped_slides`` returns [] for any root shape it does not
            # recognise (e.g. WM answers {"result": [...]}, or a provider wraps the
            # object). ``_merge_shaped_into_slides`` then builds an empty index,
            # every slide takes the untouched branch, and the per-key WARNING below
            # lives INSIDE the per-shaped-slide loop so it runs zero times. The only
            # log line left said "completed for N slides" — counting the ORIGINALS.
            # Degrade exactly like the prompt-render and WM-failure paths above
            # (ERROR + return the untouched slides), and name the shape we got so
            # the next occurrence is diagnosable from one log line.
            logger.error(
                "Content shaping produced no usable slides (WM returned %s); "
                "the deck ships UNSHAPED for %d slides and the call was still billed",
                _describe_root_shape(response.output),
                len(slides),
            )
            _publish(
                report,
                ShapingReport(
                    requested=total,
                    shaped=0,
                    unshaped=total,
                    code=SHAPING_FAILED,
                    reason=REASON_NO_USABLE_OUTPUT,
                ),
            )
            return slides
        result, shaped_count = _merge_shaped_into_slides(slides, shaped)
        unshaped = total - shaped_count
        # A model that answered for 3 of 5 slides degraded the deck by exactly 2.
        # Counting only the all-or-nothing failures above would report a
        # half-polished deck as healthy — the same lie in a smaller size.
        _publish(
            report,
            ShapingReport(
                requested=total,
                shaped=shaped_count,
                unshaped=unshaped,
                code=SHAPING_FAILED if unshaped else "",
                reason=REASON_PARTIAL_MERGE if unshaped else "",
            ),
        )
        logger.info(
            "Content shaping completed for %d slides (%d shaped, %d unshaped)",
            len(result),
            shaped_count,
            unshaped,
        )
        return result


def _render_shaping_prompt(
    slides: list[dict[str, Any]],
    brand: dict[str, Any],
    shaper_config: dict[str, Any],
) -> str | None:
    """Render the Jinja2 shaping prompt.

    Returns the rendered prompt, or ``None`` if rendering failed. ``None`` means
    "there is no usable prompt" — callers MUST skip the LLM call entirely. The
    old behaviour (returning the unrendered template) produced a prompt with no
    slide content in it at all, which the LLM happily answered with a fabricated
    deck that was then merged over the tenant's real slides.
    """
    template = _load_prompt_template()
    try:
        from jinja2 import Template

        return Template(template).render(
            slides=slides,
            brand=brand,
            tone=shaper_config.get("tone", "professional"),
            summarize=shaper_config.get("summarize", True),
            generate_speaker_notes=shaper_config.get("generate_speaker_notes", True),
            generate_transitions=shaper_config.get("generate_transitions", False),
        )
    except Exception:
        # jinja2 raises TemplateError (not OSError/RuntimeError) on a bad render.
        # A template that will not render is a code defect, not a transient, so
        # log at ERROR — but still degrade rather than aborting deck generation.
        logger.error("Jinja2 shaping-prompt rendering failed; shaping skipped", exc_info=True)
        return None


def _describe_root_shape(output: object) -> str:
    """Name the root shape WM actually returned, without logging its content.

    Slide content is tenant data, so the payload never reaches the log — only the
    root type and, for an object, its top-level keys, which is exactly what is
    needed to tell ``{"result": [...]}`` from ``{"slides": [...]}``.

    ``output`` is ``object`` rather than the untyped alias WM hands back: this
    function is reached precisely BECAUSE the payload was not the shape we
    asked for, so the annotation that forces every access through an
    ``isinstance`` check is the honest one.
    """
    if isinstance(output, dict):
        keys = sorted(str(key) for key in output)
        return f"dict with keys {keys[:10]}"
    if isinstance(output, list):
        return f"list of {len(output)} item(s) with no dict entries"
    return f"{type(output).__name__}"


def _extract_shaped_slides(output: Any) -> list[dict[str, Any]]:
    """Pull the ``slides`` list out of WM's structured output.

    WM returns the parsed object per ``output_schema``. Be permissive: accept
    a bare list (some providers return the array even with object-rooted
    schemas) or an object with a ``slides`` key.
    """
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    if isinstance(output, dict):
        slides = output.get("slides")
        if isinstance(slides, list):
            return [item for item in slides if isinstance(item, dict)]
    return []


# Expected python type of every key we merge back out of the LLM's answer.
#
# WM's ``output_schema`` is a REQUEST, not a guarantee: providers do return
# ``"content": "a string"`` or ``null`` despite the object-rooted schema above.
# Merging that wholesale replaces the slide's content DICT with a str/None, and
# the very next ``render_slide`` / ``resolve_slide_images`` call does ``.get()``
# on it -> AttributeError -> the whole deck job ends ``failed``. That is the
# exact opposite of this module's documented fail-open. ``speaker_notes`` and
# ``transition_text`` are declared strings and reach the manifest / python-pptx
# notes frame, so they get the same treatment — guarding only ``content`` would
# leave two siblings unguarded.
_SHAPED_KEY_TYPES: dict[str, type] = {
    "content": dict,
    "speaker_notes": str,
    "transition_text": str,
}


def _merge_shaped_into_slides(
    slides: list[dict[str, Any]],
    shaped: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Merge shaped content/speaker_notes/transition_text back into the slides.

    Returns ``(slides, shaped_count)``. ``shaped_count`` counts slides that
    actually took at least one value from the model — NOT slides the model
    merely named. A slide whose every key was rejected by the type guard below
    ships exactly as unpolished as one the model skipped, so counting it as
    shaped would make the report agree with the LLM's claim instead of with what
    the tenant receives.
    """
    # Range-FILTER (not clamp): drop out-of-range/negative/non-int indices.
    # Clamping with max(0, ...) would collapse distinct bad indices onto key 0
    # (dict-key collision), mis-attributing content to slide 0.
    shaped_by_index = {
        i: item
        for item in shaped
        if isinstance((i := item.get("index")), int) and 0 <= i < len(slides)
    }
    result: list[dict[str, Any]] = []
    shaped_count = 0
    for idx, slide in enumerate(slides):
        shaped_slide = shaped_by_index.get(idx)
        if shaped_slide is None:
            result.append(slide)
            continue
        merged = {**slide}
        applied = 0
        for key, expected in _SHAPED_KEY_TYPES.items():
            if key not in shaped_slide:
                continue
            value = shaped_slide[key]
            if not isinstance(value, expected):
                # Fail open PER KEY, and say so. A silent drop turns a transient
                # LLM shape error into an invisible quality regression: the deck
                # ships unpolished and nothing anywhere records that shaping was
                # asked for and did not happen.
                logger.warning(
                    "Content shaping returned %s for slide %d key %r (expected %s); "
                    "keeping the original slide value",
                    type(value).__name__,
                    idx,
                    key,
                    expected.__name__,
                )
                continue
            merged[key] = value
            applied += 1
        if applied:
            shaped_count += 1
        result.append(merged)
    return result, shaped_count
