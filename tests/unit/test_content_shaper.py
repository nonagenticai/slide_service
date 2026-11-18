"""Tests for the WM-routed slide content shaper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from w2v_common.clients import WMResponse

from src.config import Settings, clear_settings_cache
from src.services.content_shaper import (
    ContentShaper,
    _describe_root_shape,
    _load_prompt_template,
    _merge_shaped_into_slides,
    _render_shaping_prompt,
)


@pytest.fixture(autouse=True)
def _reset_settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings()


def _wm_with_response(output: object) -> AsyncMock:
    """Build an AsyncMock WMClient that returns ``output`` from .message()."""
    wm = AsyncMock()
    wm.message = AsyncMock(
        return_value=WMResponse(
            output=output,
            output_type="object",
            output_schema_used=None,
            metadata=None,
            usage_metrics=None,
        )
    )
    return wm


@pytest.fixture
def sample_slides() -> list[dict]:
    return [
        {
            "template_name": "title",
            "content": {"title": "Hello", "subtitle": "World"},
            "slot_definitions": {"title": {"type": "text"}},
        },
    ]


@pytest.fixture
def brand() -> dict:
    return {"primary_color": "#1a73e8", "font_family": "Arial", "theme": "modern"}


def test_load_prompt_template():
    """Test that the prompt template loads (either from file or default)."""
    template = _load_prompt_template()
    assert len(template) > 0
    assert "slide" in template.lower() or "Slide" in template


@pytest.mark.asyncio
async def test_shaper_disabled_returns_slides(settings, sample_slides, brand):
    """Content shaping disabled returns slides unchanged and never calls WM."""
    wm = _wm_with_response({"slides": []})
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(sample_slides, brand, {"enabled": False})

    assert result == sample_slides
    wm.message.assert_not_called()


@pytest.mark.asyncio
async def test_shaper_wm_call_success_object_output(settings, sample_slides, brand):
    """WM returns object-rooted output; shaped content is merged back in."""
    output = {
        "slides": [
            {
                "index": 0,
                "content": {"title": "Polished Hello", "subtitle": "Polished World"},
                "speaker_notes": "Introduce the topic.",
            }
        ]
    }
    wm = _wm_with_response(output)
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(
        sample_slides, brand, {"enabled": True, "tone": "professional"}
    )

    assert len(result) == 1
    assert result[0]["content"]["title"] == "Polished Hello"
    assert result[0]["speaker_notes"] == "Introduce the topic."

    # Verify WM was called with the canonical worker name + schema. The
    # per-message system_prompt was removed: slide-bullet-extractor's curated
    # system prompt lives in worker_management's seed (w2v_workers.yaml), so a
    # per-call system_prompt= would stack on the seed and double-steer.
    wm.message.assert_awaited_once()
    call_kwargs = wm.message.await_args.kwargs
    assert call_kwargs["worker_name"] == "slide-bullet-extractor"
    assert "system_prompt" not in call_kwargs
    assert call_kwargs["output_schema"]["type"] == "object"
    assert "slides" in call_kwargs["output_schema"]["properties"]


@pytest.mark.asyncio
async def test_shaper_accepts_bare_list_output(settings, sample_slides, brand):
    """Some providers return a bare list even with object-rooted schema; accept it."""
    wm = _wm_with_response([{"index": 0, "content": {"title": "Listed"}}])
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert result[0]["content"]["title"] == "Listed"


@pytest.mark.asyncio
async def test_shaper_wm_failure_returns_original(settings, sample_slides, brand):
    """WM failure returns original slides (fail-open)."""
    wm = AsyncMock()
    wm.message = AsyncMock(side_effect=RuntimeError("WM unavailable"))
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert result == sample_slides


@pytest.mark.asyncio
async def test_shaper_wm_failure_does_not_mutate_slides(settings, sample_slides, brand):
    """The WM-failure degrade hands back the very same slide objects."""
    wm = AsyncMock()
    wm.message = AsyncMock(side_effect=RuntimeError("WM unavailable"))
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert result is sample_slides
    assert result[0]["content"] == {"title": "Hello", "subtitle": "World"}


# A template that cannot be compiled/rendered — stands in for the real-world
# case where one slide's content trips the shaping template (e.g. an undefined
# attribute access). Anything raising inside _render_shaping_prompt's try block
# exercises the same path.
_BROKEN_TEMPLATE = "{{ slides | no_such_filter_exists }}"


def test_render_shaping_prompt_returns_none_on_template_failure(monkeypatch, sample_slides, brand):
    """A broken template yields None — NOT the unrendered template text.

    Returning the raw template used to send literal ``{{ slides }}`` markup to
    the LLM as the prompt: a prompt containing zero slide content.
    """
    monkeypatch.setattr(
        "src.services.content_shaper._load_prompt_template", lambda: _BROKEN_TEMPLATE
    )

    assert _render_shaping_prompt(sample_slides, brand, {"enabled": True}) is None


@pytest.mark.asyncio
async def test_shaper_prompt_render_failure_skips_wm_and_returns_original(
    monkeypatch, settings, sample_slides, brand
):
    """A prompt-render failure must SKIP shaping entirely.

    The regression: the raw template was sent to WM, the call succeeded, and the
    model's fabricated deck (invented from a contentless prompt) was merged over
    the tenant's real slides. Asserting WM is never called is the whole point.
    """
    monkeypatch.setattr(
        "src.services.content_shaper._load_prompt_template", lambda: _BROKEN_TEMPLATE
    )
    wm = _wm_with_response(
        {"slides": [{"index": 0, "content": {"title": "FABRICATED", "subtitle": "INVENTED"}}]}
    )
    shaper = ContentShaper(settings, wm_client=wm)

    original_content = sample_slides[0]["content"]
    result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    wm.message.assert_not_called()
    wm.message.assert_not_awaited()
    assert result is sample_slides
    assert result[0] is sample_slides[0]
    assert result[0]["content"] is original_content
    assert result[0]["content"] == {"title": "Hello", "subtitle": "World"}


@pytest.mark.asyncio
async def test_shaper_handles_empty_output(settings, sample_slides, brand):
    """Empty / malformed WM output leaves slides untouched."""
    wm = _wm_with_response(None)
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert result == sample_slides


def _two_slides() -> list[dict]:
    return [
        {"template_name": "title", "content": {"title": "Original 0"}},
        {"template_name": "agenda", "content": {"title": "Original 1"}},
    ]


def test_merge_valid_in_range_index_merges():
    """Happy path: a valid in-range index merges its polished content."""
    slides = _two_slides()
    shaped = [{"index": 1, "content": {"title": "Polished 1"}}]

    result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    assert result[0]["content"]["title"] == "Original 0"
    assert result[1]["content"]["title"] == "Polished 1"


def test_merge_out_of_range_index_is_dropped():
    """Indices < 0 or >= len(slides) are dropped; those slides keep originals."""
    slides = _two_slides()
    shaped = [
        {"index": -1, "content": {"title": "Negative"}},
        {"index": 5, "content": {"title": "Too big"}},
    ]

    result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    assert result[0]["content"]["title"] == "Original 0"
    assert result[1]["content"]["title"] == "Original 1"


def test_merge_negative_and_zero_do_not_collide():
    """index=-1 must NOT collapse onto key 0 — only index 0 lands on slide 0."""
    slides = _two_slides()
    shaped = [
        {"index": -1, "content": {"title": "Should be ignored"}},
        {"index": 0, "content": {"title": "Correct slide 0"}},
    ]

    result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    assert result[0]["content"]["title"] == "Correct slide 0"
    assert result[1]["content"]["title"] == "Original 1"


def test_merge_non_int_index_is_dropped():
    """A non-int index (e.g. a float 0.0 the un-typed decoder can emit) must be
    dropped, not coerced onto slot 0. Under a naive ``{item['index']: item}`` map,
    ``{0.0: x}.get(0)`` returns ``x`` (0.0 == 0), silently mis-attributing content
    to slide 0 — the isinstance(int) guard is what prevents that."""
    slides = _two_slides()
    shaped = [{"index": 0.0, "content": {"title": "Float index, must be ignored"}}]

    result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    assert result[0]["content"]["title"] == "Original 0"


# ── shaped-value SHAPE guard ──────────────────────────────────────────────────
# WM's output_schema is a request, not a guarantee. Before the guard, a shaped
# slide whose "content" was a str/None replaced the slide's content dict
# wholesale; render_slide / resolve_slide_images then called .get() on it and
# the deck job died with AttributeError. The docstring promises the ORIGINAL
# slides on any parse failure — these pin that promise for every merged key.


@pytest.mark.parametrize(
    "bad_content",
    [
        pytest.param("Polished title: Hello", id="str"),
        pytest.param(None, id="none"),
        pytest.param(["Polished", "bullets"], id="list"),
    ],
)
def test_merge_non_dict_content_keeps_original_dict(bad_content, caplog):
    """A non-dict "content" must NOT replace the original content dict."""
    slides = _two_slides()
    shaped = [{"index": 0, "content": bad_content}]

    with caplog.at_level("WARNING", logger="src.services.content_shaper"):
        result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    # The original dict survives, so downstream .get() still works.
    assert isinstance(result[0]["content"], dict)
    assert result[0]["content"] == {"title": "Original 0"}
    assert result[0]["content"].get("title") == "Original 0"
    assert result[1]["content"] == {"title": "Original 1"}
    # Fail-open must be OBSERVABLE, not silent.
    assert "Content shaping returned" in caplog.text
    assert "'content'" in caplog.text


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        pytest.param("speaker_notes", {"text": "notes"}, id="speaker_notes-dict"),
        pytest.param("speaker_notes", None, id="speaker_notes-none"),
        pytest.param("transition_text", 42, id="transition_text-int"),
        pytest.param("transition_text", None, id="transition_text-none"),
    ],
)
def test_merge_non_str_sibling_keys_are_dropped(key, bad_value, caplog):
    """speaker_notes / transition_text are declared strings — guard them too.

    Guarding only ``content`` while a sibling key stays unguarded is the
    recurring failure mode; this pins all three.
    """
    slides = _two_slides()
    shaped = [{"index": 0, "content": {"title": "Polished 0"}, key: bad_value}]

    with caplog.at_level("WARNING", logger="src.services.content_shaper"):
        result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    # The bad key is not merged at all; the good sibling key still is.
    assert key not in result[0]
    assert result[0]["content"] == {"title": "Polished 0"}
    assert f"'{key}'" in caplog.text


def test_merge_well_formed_slide_still_merges_every_key(caplog):
    """Happy path is NOT regressed: correctly-shaped values all merge, no warning."""
    slides = _two_slides()
    shaped = [
        {
            "index": 0,
            "content": {"title": "Polished 0"},
            "speaker_notes": "Say hello.",
            "transition_text": "Next up...",
        }
    ]

    with caplog.at_level("WARNING", logger="src.services.content_shaper"):
        result, _shaped_count = _merge_shaped_into_slides(slides, shaped)

    assert result[0]["content"] == {"title": "Polished 0"}
    assert result[0]["speaker_notes"] == "Say hello."
    assert result[0]["transition_text"] == "Next up..."
    assert result[0]["template_name"] == "title"
    assert result[1]["content"] == {"title": "Original 1"}
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_shaper_string_content_does_not_break_downstream(settings, sample_slides, brand):
    """End-to-end through shape_content: a str "content" must not reach callers.

    deck_builder passes slide_data["content"] straight into render_slide, which
    does content.get(...). A str there is the AttributeError that failed the deck.
    """
    wm = _wm_with_response({"slides": [{"index": 0, "content": "just a string"}]})
    shaper = ContentShaper(settings, wm_client=wm)

    result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert isinstance(result[0]["content"], dict)
    assert result[0]["content"].get("title") == "Hello"


# ---------------------------------------------------------------------------
# A metered WM call that shaped NOTHING must not log success
# ---------------------------------------------------------------------------
#
# `_extract_shaped_slides` returns [] for any root shape it does not recognise
# (WM answering {"result": [...]}, a provider wrapping the object, a null).
# `_merge_shaped_into_slides(slides, [])` then builds an empty index, every slide
# takes the `shaped_slide is None -> append(slide)` branch unchanged, and the
# per-key WARNING lives INSIDE the per-shaped-slide loop so it runs zero times.
# The deck shipped unpolished, the tenant was billed for the LLM call, and the
# only log line said "Content shaping completed for N slides" — counting the
# ORIGINALS. These tests pin the ERROR, matching the prompt-render and
# WM-exception paths, which are the file's other two fail-open degrades.


@pytest.mark.parametrize(
    ("output", "expected_fragment"),
    [
        ({"result": [{"index": 0, "content": {"title": "Polished"}}]}, "['result']"),
        ({"slides": {"index": 0}}, "['slides']"),
        ({"data": {"slides": []}}, "['data']"),
        (["not-a-dict"], "list of 1 item(s)"),
        ("a string", "str"),
        (None, "NoneType"),
    ],
)
@pytest.mark.asyncio
async def test_unusable_wm_shape_logs_error_naming_the_root(
    settings, sample_slides, brand, caplog, output, expected_fragment
):
    """A billed call that shaped nothing logs ERROR and names the shape it got."""
    shaper = ContentShaper(settings, wm_client=_wm_with_response(output))

    with caplog.at_level("ERROR", logger="src.services.content_shaper"):
        result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert result == sample_slides
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, "the no-op must be surfaced exactly once, at ERROR"
    assert "produced no usable slides" in errors[0].getMessage()
    assert expected_fragment in errors[0].getMessage()
    # ... and must NOT claim success counting the originals.
    assert "Content shaping completed" not in caplog.text


@pytest.mark.asyncio
async def test_usable_wm_shape_logs_no_error(settings, sample_slides, brand, caplog):
    """The guard must not misfire on a shape that really did merge."""
    wm = _wm_with_response({"slides": [{"index": 0, "content": {"title": "Polished"}}]})
    shaper = ContentShaper(settings, wm_client=wm)

    with caplog.at_level("INFO", logger="src.services.content_shaper"):
        result = await shaper.shape_content(sample_slides, brand, {"enabled": True})

    assert result[0]["content"]["title"] == "Polished"
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    assert "Content shaping completed for 1 slides" in caplog.text


def test_describe_root_shape_never_logs_tenant_content():
    """Slide text is tenant data — only the type and top-level keys are named."""
    described = _describe_root_shape({"result": [{"content": {"title": "SECRET"}}]})
    assert "SECRET" not in described
    assert "result" in described
