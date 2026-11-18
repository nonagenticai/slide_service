"""Async unit tests for DeckBuilder covering build_deck pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.database.models import SlideTemplate
from src.services.deck_builder import DeckBuilder

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


def _make_template(template_id: str = "t-1") -> SimpleNamespace:
    return SimpleNamespace(
        template_id=template_id,
        name="title",
        slot_definitions={"title": {"type": "text"}},
        styling_rules={"layout": "centered"},
        default_transition={"type": "fade", "duration_seconds": 0.5},
        default_timing_hint_seconds=10.0,
        layout_type="title",
        is_active=True,
    )


class _FakeScalars:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def all(self) -> list[object]:
        return self._items


class _FakeExecuteResult:
    def __init__(self, row: object = None, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)

    def scalar_one_or_none(self) -> object:
        return self._row

    def scalar(self) -> object:
        return self._row


class _SequentialDB:
    """Stub DB session that returns different results per execute call."""

    def __init__(self, results: list[_FakeExecuteResult]) -> None:
        self._results = list(results)
        self._index = 0
        self.added: list[object] = []

    async def execute(self, _stmt: object) -> _FakeExecuteResult:
        if self._index < len(self._results):
            result = self._results[self._index]
        else:
            result = _FakeExecuteResult()
        self._index += 1
        return result

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


def _as_session(db: _SequentialDB) -> AsyncSession:
    """Cast a _SequentialDB to AsyncSession for type safety in test stubs."""
    return cast(AsyncSession, db)


def _stub_storage() -> MagicMock:
    storage = MagicMock()
    storage.write_file = AsyncMock(return_value="some/path")
    storage.write_text = AsyncMock(return_value="some/manifest.json")
    storage.get_presigned_url = AsyncMock(return_value=None)
    return storage


# ---------------------------------------------------------------------------
# _resolve_templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_templates_returns_map():
    """Templates are returned as a dict keyed by template_id."""
    template = _make_template("t-1")
    db = _SequentialDB([_FakeExecuteResult(rows=[template])])

    builder = DeckBuilder(Settings())
    result = await builder._resolve_templates([{"template_id": "t-1"}], _as_session(db))

    assert "t-1" in result
    assert result["t-1"] is template


@pytest.mark.asyncio
async def test_resolve_templates_raises_on_missing():
    """ValueError is raised when a requested template is not in DB."""
    db = _SequentialDB([_FakeExecuteResult(rows=[])])

    builder = DeckBuilder(Settings())
    with pytest.raises(ValueError, match="Templates not found"):
        await builder._resolve_templates([{"template_id": "missing-t"}], _as_session(db))


# ---------------------------------------------------------------------------
# _freeze_versions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freeze_versions_creates_new_version():
    """When no existing snapshot matches, a new TemplateVersion is added."""
    template = _make_template("t-1")
    template_map = cast(dict[str, SlideTemplate], {"t-1": template})

    db = _SequentialDB(
        [
            _FakeExecuteResult(row=None),  # existing version check -> not found
            _FakeExecuteResult(row=0),  # max version number -> 0
        ]
    )

    builder = DeckBuilder(Settings())
    version_map = await builder._freeze_versions(template_map, _as_session(db))

    assert "t-1" in version_map
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_freeze_versions_reuses_existing_snapshot():
    """When an exact snapshot exists, the existing version_id is reused."""
    template = _make_template("t-1")
    template_map = cast(dict[str, SlideTemplate], {"t-1": template})

    existing_version = SimpleNamespace(version_id="v-existing")
    db = _SequentialDB(
        [
            _FakeExecuteResult(row=existing_version),  # snapshot already exists
        ]
    )

    builder = DeckBuilder(Settings())
    version_map = await builder._freeze_versions(template_map, _as_session(db))

    assert version_map["t-1"] == "v-existing"
    assert len(db.added) == 0  # nothing new added


# ---------------------------------------------------------------------------
# _attach_presigned_urls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_presigned_urls_sets_url():
    """Presigned URL is attached to each asset dict that has a storage_path."""
    storage = MagicMock()
    storage.get_presigned_url = AsyncMock(return_value="https://minio.test/presigned")

    builder = DeckBuilder(Settings(), storage_service=storage)

    manifest = {
        "assets": {
            "pptx": {"storage_path": "decks/j1/deck.pptx"},
            "pdf": {"storage_path": "decks/j1/deck.pdf"},
            "manifest": {"storage_path": "decks/j1/manifest.json"},
            "slides": [
                {"storage_path": "decks/j1/slides/slide_01.png"},
            ],
        }
    }

    await builder._attach_presigned_urls(manifest)

    assert manifest["assets"]["pptx"]["presigned_url"] == "https://minio.test/presigned"
    assert manifest["assets"]["pdf"]["presigned_url"] == "https://minio.test/presigned"
    assert manifest["assets"]["slides"][0]["presigned_url"] == "https://minio.test/presigned"


@pytest.mark.asyncio
async def test_attach_presigned_urls_skips_no_storage_path():
    """Assets without a storage_path are skipped."""
    storage = MagicMock()
    storage.get_presigned_url = AsyncMock(return_value="https://minio.test/presigned")

    builder = DeckBuilder(Settings(), storage_service=storage)

    manifest = {
        "assets": {
            "pptx": {"storage_path": ""},  # empty path
        }
    }

    await builder._attach_presigned_urls(manifest)
    assert "presigned_url" not in manifest["assets"]["pptx"]


@pytest.mark.asyncio
async def test_attach_presigned_urls_skips_none_url():
    """If get_presigned_url returns None, the key is not set."""
    storage = MagicMock()
    storage.get_presigned_url = AsyncMock(return_value=None)

    builder = DeckBuilder(Settings(), storage_service=storage)

    manifest = {"assets": {"pptx": {"storage_path": "decks/j1/deck.pptx"}}}
    await builder._attach_presigned_urls(manifest)

    assert "presigned_url" not in manifest["assets"]["pptx"]


@pytest.mark.asyncio
async def test_attach_presigned_urls_non_dict_assets():
    """Non-dict assets block is handled gracefully."""
    storage = MagicMock()
    storage.get_presigned_url = AsyncMock(return_value=None)
    builder = DeckBuilder(Settings(), storage_service=storage)

    manifest = {"assets": "not-a-dict"}
    # Should not raise
    await builder._attach_presigned_urls(manifest)


# ---------------------------------------------------------------------------
# build_deck - full pipeline (mocked internals)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_deck_full_pipeline():
    """build_deck runs the full pipeline and returns a manifest dict."""
    template = _make_template("t-1")

    # DB returns template for _resolve_templates, then existing version for _freeze_versions
    existing_version = SimpleNamespace(version_id="v-1")
    db = _SequentialDB(
        [
            _FakeExecuteResult(rows=[template]),  # _resolve_templates
            _FakeExecuteResult(row=existing_version),  # _freeze_versions: existing snapshot
        ]
    )

    storage = _stub_storage()

    request_payload = {
        "title": "Test Deck",
        "slides": [
            {
                "template_id": "t-1",
                "content": {"title": "Hello"},
                "asset_urls": {},
                "asset_placements": {},
            }
        ],
        "image_assets": [],
        "brand": {"primary_color": "#3498db"},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }

    with patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as mock_pdf:
        mock_pdf.return_value = b"%PDF-1.7 fake"
        with patch(
            "src.services.deck_builder.render_slide_png", new_callable=AsyncMock
        ) as mock_preview:
            mock_preview.return_value = b"\x89PNG\r\n\x1a\n"

            builder = DeckBuilder(Settings(), storage_service=storage)
            template_map, version_map = await builder.prepare_templates(
                request_payload["slides"], _as_session(db)
            )
            manifest = await builder.build_deck(
                "job-abc", request_payload, template_map, version_map
            )

    assert manifest["title"] == "Test Deck"
    assert manifest["slide_count"] == 1
    assert storage.write_file.called
    assert storage.write_text.called


@pytest.mark.asyncio
async def test_build_deck_with_content_shaper():
    """build_deck calls content shaper when enabled."""
    template = _make_template("t-1")
    existing_version = SimpleNamespace(version_id="v-1")
    db = _SequentialDB(
        [
            _FakeExecuteResult(rows=[template]),
            _FakeExecuteResult(row=existing_version),
        ]
    )
    storage = _stub_storage()

    request_payload = {
        "title": "Shaped Deck",
        "slides": [
            {
                "template_id": "t-1",
                "content": {"title": "AI slide"},
                "asset_urls": {},
                "asset_placements": {},
            }
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": True},
        "timing": {},
    }

    shaped_slides = [
        {
            "template_name": "title",
            "template_id": "t-1",
            "template_version_id": "v-1",
            "content": {"title": "AI-shaped"},
            "asset_urls": {},
            "asset_placements": {},
            "slot_definitions": {},
            "styling_rules": {},
            "layout_type": "title",
            "role": None,
            "timing_hint_seconds": 10.0,
            "transition": {"type": "fade", "duration_seconds": 0.5},
        }
    ]

    with patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as mock_pdf:
        mock_pdf.return_value = b"%PDF-1.7 fake"
        with patch(
            "src.services.deck_builder.render_slide_png", new_callable=AsyncMock
        ) as mock_preview:
            mock_preview.return_value = b"\x89PNG\r\n\x1a\n"
            with patch.object(
                DeckBuilder,
                "_content_shaper",
                create=True,
            ):
                builder = DeckBuilder(Settings(), storage_service=storage)
                builder._content_shaper = MagicMock()
                builder._content_shaper.shape_content = AsyncMock(return_value=shaped_slides)

                template_map, version_map = await builder.prepare_templates(
                    request_payload["slides"], _as_session(db)
                )
                manifest = await builder.build_deck(
                    "job-shaper", request_payload, template_map, version_map
                )

    assert manifest["title"] == "Shaped Deck"
    assert manifest["slide_count"] == 1


@pytest.mark.asyncio
async def test_build_deck_preserves_zero_timing_hint():
    """A stored 0.0 timing hint must survive enrichment, not be rewritten to 10.0.

    ``default_timing_hint_seconds`` is a nullable float column: only ``None``
    means "unset". A falsy-but-legitimate ``0.0`` was silently coerced to the
    10.0 default by an ``or`` conflation.
    """
    template = _make_template("t-1")
    template.default_timing_hint_seconds = 0.0
    existing_version = SimpleNamespace(version_id="v-1")
    db = _SequentialDB(
        [
            _FakeExecuteResult(rows=[template]),
            _FakeExecuteResult(row=existing_version),
        ]
    )
    storage = _stub_storage()

    request_payload = {
        "title": "Zero Timing Deck",
        "slides": [
            {
                "template_id": "t-1",
                "content": {"title": "Instant slide"},
                "asset_urls": {},
                "asset_placements": {},
            }
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        # Shaper enabled purely as an observation point: it receives the
        # enriched slide dicts verbatim before any rendering happens.
        "content_shaper": {"enabled": True},
        "timing": {},
    }

    async def _passthrough(
        slides: list[dict],
        _brand: dict,
        _config: dict,
        *,
        report: list | None = None,
    ) -> list[dict]:
        return slides

    with patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as mock_pdf:
        mock_pdf.return_value = b"%PDF-1.7 fake"
        with patch(
            "src.services.deck_builder.render_slide_png", new_callable=AsyncMock
        ) as mock_preview:
            mock_preview.return_value = b"\x89PNG\r\n\x1a\n"
            builder = DeckBuilder(Settings(), storage_service=storage)
            builder._content_shaper = MagicMock()
            builder._content_shaper.shape_content = AsyncMock(side_effect=_passthrough)

            template_map, version_map = await builder.prepare_templates(
                request_payload["slides"], _as_session(db)
            )
            await builder.build_deck("job-zero", request_payload, template_map, version_map)

    enriched_slides = builder._content_shaper.shape_content.call_args.args[0]
    assert enriched_slides[0]["timing_hint_seconds"] == 0.0


@pytest.mark.asyncio
async def test_build_deck_missing_template_raises():
    """build_deck raises ValueError when a referenced template is missing."""
    db = _SequentialDB(
        [
            _FakeExecuteResult(rows=[]),  # no templates found
        ]
    )
    storage = _stub_storage()

    request_payload = {
        "title": "Broken Deck",
        "slides": [
            {
                "template_id": "missing-template",
                "content": {},
                "asset_urls": {},
                "asset_placements": {},
            }
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "16:9",
        "content_shaper": {"enabled": False},
        "timing": {},
    }

    builder = DeckBuilder(Settings(), storage_service=storage)
    with pytest.raises(ValueError, match="Templates not found"):
        await builder.prepare_templates(request_payload["slides"], _as_session(db))


@pytest.mark.asyncio
async def test_build_deck_4by3_aspect_ratio():
    """build_deck accepts 4:3 aspect ratio."""
    template = _make_template("t-1")
    existing_version = SimpleNamespace(version_id="v-1")
    db = _SequentialDB(
        [
            _FakeExecuteResult(rows=[template]),
            _FakeExecuteResult(row=existing_version),
        ]
    )
    storage = _stub_storage()

    request_payload = {
        "title": "4:3 Deck",
        "slides": [
            {
                "template_id": "t-1",
                "content": {"title": "Slide"},
                "asset_urls": {},
                "asset_placements": {},
            }
        ],
        "image_assets": [],
        "brand": {},
        "aspect_ratio": "4:3",
        "content_shaper": {"enabled": False},
        "timing": {},
    }

    with patch("src.services.deck_builder.convert_pptx_to_pdf", new_callable=AsyncMock) as mock_pdf:
        mock_pdf.return_value = b"%PDF-1.7"
        with patch(
            "src.services.deck_builder.render_slide_png", new_callable=AsyncMock
        ) as mock_preview:
            mock_preview.return_value = b"\x89PNG\r\n\x1a\n"

            builder = DeckBuilder(Settings(), storage_service=storage)
            template_map, version_map = await builder.prepare_templates(
                request_payload["slides"], _as_session(db)
            )
            manifest = await builder.build_deck(
                "job-4by3", request_payload, template_map, version_map
            )

    assert manifest["slide_count"] == 1
