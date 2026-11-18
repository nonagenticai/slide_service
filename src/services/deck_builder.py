"""Orchestrates deck generation pipeline."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any
from uuid import uuid7

from pptx import Presentation
from pptx.presentation import Presentation as PresentationType
from pptx.util import Inches
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from w2v_common.clients import WMClient

from src.config import Settings
from src.database.models import SlideTemplate, TemplateVersion
from src.services.content_shaper import ContentShaper, ShapingReport
from src.services.html_slide_renderer import (
    SlideContentReport,
    SlideImageReport,
    render_slide_png,
)
from src.services.image_layout import ImageSlotKind, classify_image_slot
from src.services.manifest_builder import build_manifest
from src.services.pdf_converter import convert_pptx_to_pdf
from src.services.storage_service import StorageService
from src.services.template_renderer import render_slide
from src.services.template_snapshot import snapshot_hash_for

logger = logging.getLogger(__name__)


class DeckBuilder:
    """Orchestrates the full deck generation pipeline."""

    def __init__(
        self,
        settings: Settings,
        storage_service: StorageService | None = None,
        wm_client: WMClient | None = None,
    ) -> None:
        self._settings = settings
        self._content_shaper = ContentShaper(settings, wm_client=wm_client)
        self._storage = storage_service or StorageService()

    async def prepare_templates(
        self,
        slides_data: list[dict],
        db: AsyncSession,
    ) -> tuple[dict[str, SlideTemplate], dict[str, str]]:
        """DB phase of deck generation: resolve templates + freeze versions.

        Runs inside a short-lived session that the caller commits and closes
        *before* the long (LLM / render / PDF / storage) generation phase, so no
        DB connection is held idle-in-transaction across ``build_deck``. The
        returned ``SlideTemplate`` objects stay readable after the session closes
        because the session uses ``expire_on_commit=False`` and every column is
        already loaded by ``_resolve_templates`` — ``build_deck`` only reads
        columns, never lazy-loads.
        """
        template_map = await self._resolve_templates(slides_data, db)
        version_map = await self._freeze_versions(template_map, db)
        return template_map, version_map

    async def build_deck(
        self,
        job_id: str,
        request_payload: dict[str, Any],
        template_map: dict[str, SlideTemplate],
        version_map: dict[str, str],
    ) -> dict[str, Any]:
        """Run the deck generation pipeline (steps 3-8; holds NO DB connection).

        Steps 1-2 (resolve templates + freeze versions) run separately in
        ``prepare_templates`` inside a short DB session that is committed and
        closed before this method, so no connection is held idle across the long
        LLM / render / PDF / storage work below.

        3. Content shaping (if enabled)
        4. Render slides via template_renderer
        5. Export PPTX and convert PDF
        6. Build manifest
        7. Write artifacts to storage
        8. Return manifest dict
        """
        title = request_payload["title"]
        slides_data = request_payload["slides"]
        image_assets = request_payload.get("image_assets", [])
        brand = request_payload.get("brand", {})
        # Optional design-system tokens (surface / type scale / grid). Absent => the
        # HTML templates fall back to their literals and the render is unchanged.
        raw_tokens = request_payload.get("tokens")
        tokens: dict[str, str] | None = None
        if isinstance(raw_tokens, dict):
            tokens = {str(k): str(v) for k, v in raw_tokens.items()}
        aspect_ratio = request_payload.get("aspect_ratio", "16:9")
        render_scale = request_payload.get("render_scale")
        shaper_config = request_payload.get("content_shaper", {})
        timing = request_payload.get("timing", {})
        asset_catalog = self._build_asset_catalog(image_assets)

        # 3. Prepare slide data for content shaping
        # Explicit annotation: the literals below are heterogeneous (str, dict,
        # float, None), so an inferred element type collapses to a value union
        # and every downstream `slide_data[...]` read fails to match a
        # concrete parameter type. This also matches ContentShaper.shape_content,
        # which takes and returns list[dict[str, Any]].
        enriched_slides: list[dict[str, Any]] = []
        for slide_req in slides_data:
            template_id = slide_req["template_id"]
            template = template_map.get(template_id)
            if not template:
                raise ValueError(f"Template {template_id} not found")
            content, asset_urls, asset_placements = self._apply_slide_asset_contract(
                slide_req,
                asset_catalog,
            )

            enriched_slides.append(
                {
                    "template_name": template.name,
                    "template_id": template_id,
                    "template_version_id": version_map.get(template_id, ""),
                    "content": content,
                    "asset_urls": asset_urls,
                    "asset_placements": asset_placements,
                    "slot_definitions": template.slot_definitions,
                    "styling_rules": template.styling_rules,
                    "layout_type": template.layout_type,
                    "role": slide_req.get("role"),
                    "timing_hint_seconds": (
                        template.default_timing_hint_seconds
                        if template.default_timing_hint_seconds is not None
                        else 10.0
                    ),
                    "transition": template.default_transition
                    or {
                        "type": "fade",
                        "duration_seconds": 0.5,
                    },
                }
            )

        # 4. Content shaping (optional)
        #
        # `shape_content` fails OPEN by design -- a transient WM failure returns
        # the raw slides rather than losing a paid deck -- so the ONLY thing that
        # ever recorded "this deck shipped unsummarised, with no speaker notes,
        # and the WM call was still billed" was an ERROR log line. The sink below
        # carries that fact onto the manifest, where the orchestrator reads it
        # without parsing logs. It never changes the job's status.
        shaping_sink: list[ShapingReport] = []
        enriched_slides = await self._content_shaper.shape_content(
            enriched_slides, brand, shaper_config, report=shaping_sink
        )
        shaping_report = shaping_sink[0] if shaping_sink else ShapingReport()

        # 5. Render slides
        prs = Presentation()
        self._apply_aspect_ratio(prs, aspect_ratio)
        for slide_data in enriched_slides:
            # Offload sync rendering (which performs blocking image I/O via
            # `httpx.Client`) to a worker thread so the event loop is not stalled.
            await asyncio.to_thread(
                render_slide,
                prs,
                layout_type=slide_data["layout_type"],
                content=slide_data["content"],
                brand=brand,
                styling_rules=slide_data.get("styling_rules", {}),
            )

        # 6. Render and store slide previews used by the manifest.
        slide_assets: list[dict[str, Any]] = []
        deck_image_report = SlideImageReport()
        deck_content_report = SlideContentReport()
        external_image_refs = self._collect_request_image_assets(image_assets, enriched_slides)
        for idx, slide_data in enumerate(enriched_slides):
            preview_path = f"decks/{job_id}/slides/slide_{idx + 1:02d}.png"
            report_sink: list[SlideImageReport] = []
            content_sink: list[SlideContentReport] = []
            preview_bytes = await render_slide_png(
                layout_type=slide_data["layout_type"],
                content=slide_data["content"],
                brand=brand,
                aspect_ratio=aspect_ratio,
                scale=render_scale,
                tokens=tokens,
                image_report=report_sink,
                content_report=content_sink,
            )
            slide_report = report_sink[0] if report_sink else SlideImageReport()
            deck_image_report = deck_image_report.merge(slide_report)
            slide_content = content_sink[0] if content_sink else SlideContentReport()
            deck_content_report = deck_content_report.merge(slide_content)
            await self._storage.write_file(preview_path, preview_bytes, content_type="image/png")
            slide_assets.append(
                {
                    "index": idx,
                    "role": slide_data.get("role"),
                    "template_name": slide_data.get("template_name", "unknown"),
                    "storage_path": preview_path,
                    "download_path": f"/api/decks/{job_id}/slides/{idx}.png",
                    "download_url": f"/api/decks/{job_id}/slides/{idx}.png",
                    "filename": f"{job_id}_slide_{idx + 1:02d}.png",
                    "content_type": "image/png",
                    "source_images": self._collect_image_refs(slide_data.get("content", {})),
                    # Per-slide provenance for the imageless-deck signal below.
                    "image_slots_requested": slide_report.requested,
                    "image_slots_failed": slide_report.failed,
                    "image_slots_unclassified": slide_report.unclassified,
                    "image_slots_undrawn": slide_report.undrawn,
                    # Same provenance for LIST content. The rendered PNG carries a
                    # marker a human can see; these are what a programmatic
                    # consumer (the video pipeline) reads without OCR.
                    "content_entries_offered": slide_content.offered,
                    "content_entries_dropped": slide_content.dropped,
                }
            )

        self._guard_image_slots(job_id, deck_image_report)

        # 7. Export PPTX and convert PDF
        pptx_buffer = io.BytesIO()
        prs.save(pptx_buffer)
        pptx_bytes = pptx_buffer.getvalue()
        pdf_bytes = await convert_pptx_to_pdf(pptx_bytes)

        # 8. Build and persist manifest + deck artifacts.
        manifest = build_manifest(
            job_id=job_id,
            title=title,
            aspect_ratio=aspect_ratio,
            brand=brand,
            slides=enriched_slides,
            slide_assets=slide_assets,
            external_images=external_image_refs,
            timing=timing,
        )
        # Deck-level image provenance. Extra keys on the manifest dict are dropped
        # by ``DeckManifestResponse``, so both counters are declared fields there.
        manifest["image_slots_requested"] = deck_image_report.requested
        manifest["image_slots_failed"] = deck_image_report.failed
        manifest["image_slots_unclassified"] = deck_image_report.unclassified
        manifest["image_slots_undrawn"] = deck_image_report.undrawn
        manifest["content_entries_offered"] = deck_content_report.offered
        manifest["content_entries_dropped"] = deck_content_report.dropped
        # LLM-shaping provenance. Same contract as the counters above: declared
        # fields on ``DeckManifestResponse`` (undeclared keys are dropped), and
        # never a reason to fail or downgrade the job.
        manifest["shaping_slides_requested"] = shaping_report.requested
        manifest["shaping_slides_shaped"] = shaping_report.shaped
        manifest["shaping_slides_unshaped"] = shaping_report.unshaped
        manifest["shaping_degraded_code"] = shaping_report.code
        manifest["shaping_degraded_reason"] = shaping_report.reason
        if shaping_report.code:
            logger.warning(
                "Deck %s: content shaping degraded (%s/%s); %d of %d slides ship "
                "UNSHAPED and the WM call was still billed",
                job_id,
                shaping_report.code,
                shaping_report.reason,
                shaping_report.unshaped,
                shaping_report.requested,
            )
        if deck_content_report.dropped:
            logger.warning(
                "Deck %s: %d of %d list entries were unrenderable (%s); the slides "
                "carry a degraded marker and the manifest carries the count",
                job_id,
                deck_content_report.dropped,
                deck_content_report.offered,
                ", ".join(dict.fromkeys(deck_content_report.fields)),
            )

        pptx_path = f"decks/{job_id}/deck.pptx"
        pdf_path = f"decks/{job_id}/deck.pdf"
        manifest_path = f"decks/{job_id}/manifest.json"

        await self._storage.write_file(
            pptx_path,
            pptx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        await self._storage.write_file(
            pdf_path,
            pdf_bytes,
            content_type="application/pdf",
        )
        await self._attach_presigned_urls(manifest)
        await self._storage.write_text(
            manifest_path,
            json.dumps(manifest, indent=2),
            content_type="application/json",
        )

        manifest["slide_count"] = len(enriched_slides)
        logger.info(
            "Deck %s built: %d slides, pptx=%d bytes, pdf=%d bytes",
            job_id,
            len(enriched_slides),
            len(pptx_bytes),
            len(pdf_bytes),
        )

        return manifest

    def _guard_image_slots(self, job_id: str, report: SlideImageReport) -> None:
        """Hard-fail a deck that asked for images and got NONE of them.

        Threshold reasoning (deliberate, and deliberately narrow):

        * ``requested == 0`` — an intended text-only deck. Never fails.
        * ``0 < failed < requested`` — the deck is mostly what was asked for and
          is genuinely useful. Failing it would throw away good work, and the
          per-slide + deck-level counters now make the loss visible to the caller
          (manifest ``image_slots_failed``, plus a WARNING per slot). Ships.
        * ``unclassified > 0`` — an image-ish key held something that is neither a
          reference nor a registered layout hint. Never fails the deck (a new
          styling key must not take prod down) but is never silently treated as a
          satisfied slot either: it is logged and carried in the manifest.
        * ``undrawn > 0`` — a slot carried a real ref that no renderer in the
          layout family draws. Never fails the deck, because this guard is about
          images that FAILED TO LOAD and an undrawn slot was never fetched at
          all: the ref may be live, so failing here would be a hard-fail on a
          fact never established. It is a TEMPLATE defect, so it is reported as
          one — a distinct WARNING naming the slots, plus ``image_slots_undrawn``
          on the manifest — which is what lets an operator tell "your logo URL is
          dead" from "this template declares a slot nothing draws".
        * ``failed == requested > 0`` — EVERY visual is gone. The output is a
          text-only deck that is byte-indistinguishable from one the caller
          deliberately asked for, so shipping it 200/``completed`` is a lie about
          what was produced. This is also the *common* prod shape rather than an
          exotic one (presigned MinIO URLs expire and are then uniformly dead),
          which is exactly why it must be loud. Fails.

        The "all failed" line is chosen over a percentage because it is the only
        threshold that needs no tuning and cannot misfire on a mixed deck: it
        fires precisely when the deck carries zero of the imagery it requested.
        """
        if report.unclassified:
            logger.warning(
                "Deck %s: %d image-ish slot(s) (%s) were neither a reference nor a "
                "known layout hint. They are NOT counted as satisfied and NOT counted "
                "as failed — register the key in image_layout.IMAGE_LAYOUT_HINT_KEYS "
                "if it is styling, or pass a resolvable ref if it is an image.",
                job_id,
                report.unclassified,
                ", ".join(report.unclassified_slots[:5]),
            )
        if report.undrawn:
            logger.warning(
                "Deck %s: %d image slot(s) (%s) carried a reference that no renderer "
                "in their layout family draws. The deck is NOT failed — nothing was "
                "fetched, so this is not a lost image — but the template is wrong: "
                "either move the slot to a family that draws it, or stop declaring "
                "it. See manifest image_slots_undrawn.",
                job_id,
                report.undrawn,
                ", ".join(report.undrawn_slots[:5]),
            )
        if not report.requested or report.failed < report.requested:
            return
        raise ValueError(
            f"Deck {job_id}: every requested image failed to load "
            f"({report.failed}/{report.requested} slots) — the deck would ship "
            f"text-only and look deliberate. Failed refs: "
            f"{', '.join(report.failed_refs[:5])}"
        )

    def _build_asset_catalog(self, image_assets: list[dict[str, Any]] | Any) -> dict[str, str]:
        catalog: dict[str, str] = {}
        for asset in image_assets if isinstance(image_assets, list) else []:
            if not isinstance(asset, dict):
                continue
            asset_id = str(asset.get("asset_id") or "").strip()
            image_url = str(asset.get("image_url") or "").strip()
            if asset_id and image_url:
                catalog[asset_id] = image_url
        return catalog

    def _resolve_asset_reference(self, reference: Any, asset_catalog: dict[str, str]) -> str:
        asset_ref = str(reference or "").strip()
        if not asset_ref:
            return ""
        normalized = asset_ref.removeprefix("asset://").removeprefix("asset:")
        return asset_catalog.get(normalized, asset_ref)

    def _apply_slide_asset_contract(
        self,
        slide_req: dict[str, Any],
        asset_catalog: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        content = dict(slide_req.get("content", {}))
        asset_urls: dict[str, str] = {}
        for slot_name, value in list(content.items()):
            slot = str(slot_name or "").strip()
            if not slot or not isinstance(value, str):
                continue
            # A LAYOUT HINT (``image_fit="contain"``) matches the image-slot
            # substrings but is a rendering semantic, not a ref: resolving it
            # against the asset catalog published "contain" as an asset URL.
            # UNCLASSIFIED still goes through the catalog — that lookup is
            # precisely what turns a bare asset id into a real reference.
            kind = classify_image_slot(slot, value)
            if kind is not ImageSlotKind.REFERENCE and kind is not ImageSlotKind.UNCLASSIFIED:
                continue
            resolved = self._resolve_asset_reference(value, asset_catalog)
            if not resolved:
                continue
            content[slot] = resolved
            asset_urls[slot] = resolved

        raw_asset_urls = slide_req.get("asset_urls", {})
        if isinstance(raw_asset_urls, dict):
            for slot_name, reference in raw_asset_urls.items():
                slot = str(slot_name or "").strip()
                resolved = self._resolve_asset_reference(reference, asset_catalog)
                if not slot or not resolved:
                    continue
                asset_urls[slot] = resolved
                content[slot] = resolved

        raw_placements = slide_req.get("asset_placements", {})
        asset_placements = dict(raw_placements) if isinstance(raw_placements, dict) else {}
        if asset_placements:
            content["asset_placements"] = asset_placements

        return content, asset_urls, asset_placements

    async def _attach_presigned_urls(self, manifest: dict[str, Any]) -> None:
        assets = manifest.get("assets", {})
        if not isinstance(assets, dict):
            return

        for asset_name in ("pptx", "pdf", "manifest"):
            asset = assets.get(asset_name)
            if isinstance(asset, dict):
                await self._attach_presigned_url(asset)

        slide_assets = assets.get("slides", [])
        for slide_asset in slide_assets if isinstance(slide_assets, list) else []:
            if isinstance(slide_asset, dict):
                await self._attach_presigned_url(slide_asset)

    async def _attach_presigned_url(self, asset: dict[str, Any]) -> None:
        storage_path = str(asset.get("storage_path") or "").strip()
        if not storage_path:
            return
        presigned_url = await self._storage.get_presigned_url(storage_path)
        if presigned_url:
            asset["presigned_url"] = presigned_url

    def _collect_request_image_assets(
        self,
        image_assets: list[dict[str, Any]],
        slides: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        external_assets: list[dict[str, Any]] = []
        seen: set[str] = set()

        for asset in image_assets if isinstance(image_assets, list) else []:
            if not isinstance(asset, dict):
                continue
            image_url = str(asset.get("image_url") or "").strip()
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            external_assets.append(asset)

        for slide in slides:
            refs = self._collect_image_refs(slide.get("content", {}))
            for ref in refs:
                if ref in seen:
                    continue
                seen.add(ref)
                external_assets.append(
                    {
                        "asset_id": f"slide-ref-{len(external_assets)}",
                        "label": "",
                        "kind": "image",
                        "image_url": ref,
                        "metadata": {"derived": True},
                    }
                )
        return external_assets

    def _collect_image_refs(self, value: Any, key: str = "") -> list[str]:
        refs: list[str] = []
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                refs.extend(self._collect_image_refs(child_value, child_key))
            return refs
        if isinstance(value, list):
            for child in value:
                refs.extend(self._collect_image_refs(child, key))
            return refs
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return refs
            if classify_image_slot(key, text) is ImageSlotKind.REFERENCE:
                refs.append(text)
        return refs

    async def _resolve_templates(
        self,
        slides_data: list[dict],
        db: AsyncSession,
    ) -> dict[str, SlideTemplate]:
        """Look up all referenced templates from DB."""
        template_ids = {s["template_id"] for s in slides_data}
        stmt = select(SlideTemplate).where(
            SlideTemplate.template_id.in_(template_ids),
            SlideTemplate.is_active.is_(True),
        )
        result = await db.execute(stmt)
        templates = result.scalars().all()

        template_map: dict[str, SlideTemplate] = {str(t.template_id): t for t in templates}

        missing = template_ids - set(template_map.keys())
        if missing:
            raise ValueError(f"Templates not found: {missing}")

        return template_map

    async def _freeze_versions(
        self,
        template_map: dict[str, SlideTemplate],
        db: AsyncSession,
    ) -> dict[str, str]:
        """Freeze current template state into template_versions."""
        version_map: dict[str, str] = {}

        for template_id, template in template_map.items():
            # Single-sourced with the startup freeze in ``src/seed/seed_templates``.
            # A platform (``is_system``) template is owned by the platform tenant,
            # so the INSERT below is REFUSED by the ``tenant_isolation_ins`` policy
            # when a tenant renders it — the startup freeze is what guarantees the
            # lookup hits and this loop never reaches that INSERT for such a row.
            snapshot_hash = snapshot_hash_for(template)

            # Check if this exact snapshot already exists
            existing = await db.execute(
                select(TemplateVersion).where(
                    TemplateVersion.template_id == template_id,
                    TemplateVersion.snapshot_hash == snapshot_hash,
                )
            )
            existing_version = existing.scalar_one_or_none()

            if existing_version:
                version_map[template_id] = str(existing_version.version_id)
            else:
                # Get next version number
                max_version = await db.execute(
                    select(func.max(TemplateVersion.version_number)).where(
                        TemplateVersion.template_id == template_id
                    )
                )
                current_max = max_version.scalar() or 0

                version_id = str(uuid7())
                new_version = TemplateVersion(
                    version_id=version_id,
                    template_id=template_id,
                    version_number=current_max + 1,
                    snapshot_hash=snapshot_hash,
                    slot_definitions=template.slot_definitions,
                    styling_rules=template.styling_rules,
                    default_transition=template.default_transition,
                    default_timing_hint_seconds=template.default_timing_hint_seconds,
                )
                db.add(new_version)
                version_map[template_id] = version_id

        await db.flush()
        return version_map

    def _apply_aspect_ratio(self, prs: PresentationType, aspect_ratio: str) -> None:
        """Apply the requested slide size before rendering any content."""
        normalized = str(aspect_ratio or "16:9").strip()
        if normalized == "16:9":
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)
            return
        if normalized == "4:3":
            prs.slide_width = Inches(10)
            prs.slide_height = Inches(7.5)
            return
        logger.warning("Unknown aspect ratio %s, falling back to 16:9", aspect_ratio)
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
