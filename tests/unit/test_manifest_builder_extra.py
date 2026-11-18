"""Extra tests for manifest builder covering normalization and structure details."""

from __future__ import annotations

from src.services.manifest_builder import _normalize_slide_assets, build_manifest


class TestNormalizeSlideAssets:
    def test_copies_download_url_to_download_path(self):
        """Both download_url and download_path should be set to the same value."""
        assets = [
            {"index": 0, "download_url": "/api/decks/j1/slides/0.png", "storage_path": "x"},
        ]
        result = _normalize_slide_assets(assets)
        assert len(result) == 1
        assert result[0]["download_url"] == "/api/decks/j1/slides/0.png"
        assert result[0]["download_path"] == "/api/decks/j1/slides/0.png"
        assert result[0]["index"] == 0
        assert result[0]["storage_path"] == "x"

    def test_fallback_to_download_path_when_download_url_missing(self):
        """When download_url is absent, download_path is used for both fields."""
        assets = [
            {"index": 0, "download_path": "/fallback"},
        ]
        result = _normalize_slide_assets(assets)
        assert result[0]["download_url"] == "/fallback"
        assert result[0]["download_path"] == "/fallback"

    def test_none_returns_empty_list(self):
        """None input returns an empty list."""
        result = _normalize_slide_assets(None)
        assert result == []
        assert isinstance(result, list)

    def test_empty_list_returns_empty_list(self):
        """Empty list input returns empty list."""
        result = _normalize_slide_assets([])
        assert result == []

    def test_preserves_extra_keys(self):
        """Extra keys on asset dicts are preserved through normalization."""
        assets = [
            {
                "index": 2,
                "download_url": "/api/decks/j1/slides/2.png",
                "storage_path": "decks/j1/slides/slide_03.png",
                "content_type": "image/png",
                "file_size": 12345,
            },
        ]
        result = _normalize_slide_assets(assets)
        assert result[0]["content_type"] == "image/png"
        assert result[0]["file_size"] == 12345

    def test_multiple_assets_all_normalized(self):
        """Multiple assets are all normalized independently."""
        assets = [
            {"index": 0, "download_url": "/a"},
            {"index": 1, "download_path": "/b"},
            {"index": 2, "download_url": "/c", "download_path": "/old"},
        ]
        result = _normalize_slide_assets(assets)
        assert len(result) == 3
        assert result[0]["download_url"] == "/a"
        assert result[0]["download_path"] == "/a"
        assert result[1]["download_url"] == "/b"
        assert result[1]["download_path"] == "/b"
        # download_url is set from download_url; download_path keeps original (setdefault)
        assert result[2]["download_url"] == "/c"
        assert result[2]["download_path"] == "/old"


class TestBuildManifestSlideAssets:
    def test_manifest_embeds_slide_assets_in_assets_block(self):
        """Slide assets appear under manifest.assets.slides with correct structure."""
        slide_assets = [
            {
                "index": 0,
                "download_url": "/api/decks/j1/slides/0.png",
                "storage_path": "decks/j1/slides/slide_01.png",
            },
            {
                "index": 1,
                "download_url": "/api/decks/j1/slides/1.png",
                "storage_path": "decks/j1/slides/slide_02.png",
            },
        ]
        manifest = build_manifest(
            job_id="j1",
            title="Test",
            aspect_ratio="16:9",
            brand={},
            slides=[
                {"template_name": "title", "content": {}},
                {"template_name": "agenda", "content": {}},
            ],
            slide_assets=slide_assets,
        )
        assert len(manifest["assets"]["slides"]) == 2
        assert manifest["assets"]["slides"][0]["index"] == 0
        assert manifest["assets"]["slides"][0]["download_url"] == "/api/decks/j1/slides/0.png"
        assert manifest["assets"]["slides"][0]["download_path"] == "/api/decks/j1/slides/0.png"
        assert manifest["assets"]["slides"][1]["index"] == 1

    def test_manifest_without_slide_assets_has_empty_slides_array(self):
        """When no slide_assets provided, assets.slides is an empty list."""
        manifest = build_manifest(
            job_id="j1",
            title="Test",
            aspect_ratio="16:9",
            brand={},
            slides=[{"template_name": "title", "content": {}}],
        )
        assert manifest["assets"]["slides"] == []


class TestBuildManifestExternalImages:
    def test_manifest_with_external_images(self):
        """External image assets appear in manifest.assets.external_images."""
        external = [
            {"asset_id": "ext-1", "image_url": "https://example.com/img.png"},
            {"asset_id": "ext-2", "image_url": "https://example.com/banner.jpg"},
        ]
        manifest = build_manifest(
            job_id="j1",
            title="Test",
            aspect_ratio="16:9",
            brand={},
            slides=[{"template_name": "title", "content": {}}],
            external_images=external,
        )
        assert len(manifest["assets"]["external_images"]) == 2
        assert manifest["assets"]["external_images"][0]["asset_id"] == "ext-1"
        assert (
            manifest["assets"]["external_images"][0]["image_url"] == "https://example.com/img.png"
        )
        assert manifest["assets"]["external_images"][1]["asset_id"] == "ext-2"

    def test_manifest_without_external_images_has_empty_list(self):
        """When no external_images provided, the key has an empty list."""
        manifest = build_manifest(
            job_id="j1",
            title="Test",
            aspect_ratio="16:9",
            brand={},
            slides=[{"template_name": "title", "content": {}}],
        )
        assert manifest["assets"]["external_images"] == []

    def test_manifest_structure_has_all_required_top_level_keys(self):
        """Verify the manifest has all required top-level keys."""
        manifest = build_manifest(
            job_id="j-abc",
            title="Full Structure",
            aspect_ratio="4:3",
            brand={"primary_color": "#ff0000"},
            slides=[{"template_name": "cta", "content": {"title": "Go"}}],
        )
        expected_keys = {
            "manifest_version",
            "deck_id",
            "title",
            "aspect_ratio",
            "brand",
            "slide_count",
            "total_estimated_duration_seconds",
            "slides",
            "assets",
        }
        assert set(manifest.keys()) >= expected_keys
        assert manifest["manifest_version"] == "1.0"
        assert manifest["deck_id"] == "j-abc"
        assert manifest["aspect_ratio"] == "4:3"
        assert manifest["brand"] == {"primary_color": "#ff0000"}
        assert manifest["slide_count"] == 1

    def test_manifest_assets_contain_pptx_pdf_manifest_paths(self):
        """The assets block should have pptx, pdf, and manifest download paths."""
        manifest = build_manifest(
            job_id="my-job",
            title="T",
            aspect_ratio="16:9",
            brand={},
            slides=[{"template_name": "title", "content": {}}],
        )
        pptx = manifest["assets"]["pptx"]
        assert pptx["storage_path"] == "decks/my-job/deck.pptx"
        assert pptx["download_url"] == "/api/decks/my-job/pptx"

        pdf = manifest["assets"]["pdf"]
        assert pdf["storage_path"] == "decks/my-job/deck.pdf"
        assert pdf["download_url"] == "/api/decks/my-job/pdf"

        meta = manifest["assets"]["manifest"]
        assert meta["storage_path"] == "decks/my-job/manifest.json"
        assert meta["download_url"] == "/api/decks/my-job/manifest"
