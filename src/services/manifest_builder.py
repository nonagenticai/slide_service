"""Generates consumer-agnostic JSON manifest for a deck."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _build_download_asset(
    *,
    storage_path: str,
    download_path: str,
    filename: str,
    content_type: str,
) -> dict[str, str]:
    return {
        "storage_path": storage_path,
        "download_path": download_path,
        "download_url": download_path,
        "filename": filename,
        "content_type": content_type,
    }


def _normalize_slide_assets(slide_assets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized_assets: list[dict[str, Any]] = []
    for asset in slide_assets or []:
        normalized = dict(asset)
        download_url = str(normalized.get("download_url") or normalized.get("download_path") or "")
        if download_url:
            normalized["download_url"] = download_url
            normalized.setdefault("download_path", download_url)
        normalized_assets.append(normalized)
    return normalized_assets


def build_manifest(
    job_id: str,
    title: str,
    aspect_ratio: str,
    brand: dict[str, Any],
    slides: list[dict[str, Any]],
    slide_assets: list[dict[str, Any]] | None = None,
    external_images: list[dict[str, Any]] | None = None,
    timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a consumer-agnostic manifest from deck generation results.

    Returns a dict matching the DeckManifestResponse schema.
    """
    default_slide_duration = 15.0
    if timing:
        default_slide_duration = timing.get("default_slide_duration_seconds", 15.0)

    manifest_slides = []
    total_duration = 0.0

    for idx, slide_data in enumerate(slides):
        slide_duration = slide_data.get("timing_hint_seconds", default_slide_duration)
        total_duration += slide_duration

        manifest_slide: dict[str, Any] = {
            "index": idx,
            "role": slide_data.get("role"),
            "template_name": slide_data.get("template_name", "unknown"),
            "template_version_id": slide_data.get("template_version_id"),
            "content": slide_data.get("content", {}),
            "asset_urls": slide_data.get("asset_urls", {}),
            "asset_placements": slide_data.get("asset_placements", {}),
            "speaker_notes": slide_data.get("speaker_notes", ""),
            "timing_hint_seconds": slide_duration,
            "transition": slide_data.get("transition", {"type": "fade", "duration_seconds": 0.5}),
            "animations": slide_data.get("animations", []),
        }
        manifest_slides.append(manifest_slide)

    normalized_slide_assets = _normalize_slide_assets(slide_assets)

    return {
        "manifest_version": "1.0",
        "deck_id": job_id,
        "title": title,
        "aspect_ratio": aspect_ratio,
        "slide_count": len(manifest_slides),
        "total_estimated_duration_seconds": total_duration,
        "brand": brand,
        "assets": {
            "pptx": _build_download_asset(
                storage_path=f"decks/{job_id}/deck.pptx",
                download_path=f"/api/decks/{job_id}/pptx",
                filename=f"{job_id}.pptx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                ),
            ),
            "pdf": _build_download_asset(
                storage_path=f"decks/{job_id}/deck.pdf",
                download_path=f"/api/decks/{job_id}/pdf",
                filename=f"{job_id}.pdf",
                content_type="application/pdf",
            ),
            "manifest": _build_download_asset(
                storage_path=f"decks/{job_id}/manifest.json",
                download_path=f"/api/decks/{job_id}/manifest",
                filename=f"{job_id}.manifest.json",
                content_type="application/json",
            ),
            "slides": normalized_slide_assets,
            "external_images": external_images or [],
        },
        "slides": manifest_slides,
    }
