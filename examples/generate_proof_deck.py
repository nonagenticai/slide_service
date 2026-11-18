"""Generate a proof deck that exercises the image-heavy slide templates."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches

from src.services.template_renderer import render_slide


def _draw_mock_screen(path: Path, title: str, accent: tuple[int, int, int], subtitle: str) -> None:
    image = Image.new("RGB", (1600, 900), (245, 247, 250))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (42, 42, 1558, 858), radius=34, fill=(255, 255, 255), outline=(220, 226, 234), width=4
    )
    draw.rounded_rectangle((88, 92, 1512, 150), radius=20, fill=accent)
    draw.text((128, 112), title, fill=(255, 255, 255))
    draw.text((128, 170), subtitle, fill=(84, 96, 112))

    draw.rounded_rectangle(
        (128, 240, 560, 760), radius=22, fill=(248, 250, 252), outline=(228, 233, 240), width=3
    )
    draw.rounded_rectangle(
        (612, 240, 1464, 520), radius=22, fill=(248, 250, 252), outline=(228, 233, 240), width=3
    )
    draw.rounded_rectangle(
        (612, 560, 1012, 760), radius=22, fill=(248, 250, 252), outline=(228, 233, 240), width=3
    )
    draw.rounded_rectangle(
        (1064, 560, 1464, 760), radius=22, fill=(248, 250, 252), outline=(228, 233, 240), width=3
    )

    for idx in range(5):
        top = 276 + idx * 86
        draw.rounded_rectangle((164, top, 524, top + 54), radius=16, fill=(232, 237, 244))

    for idx in range(3):
        left = 658 + idx * 248
        height = [160, 220, 120][idx]
        draw.rounded_rectangle((left, 460 - height, left + 108, 460), radius=16, fill=accent)

    image.save(path, format="PNG")


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    output_dir = base_dir / "test_output"
    asset_dir = output_dir / "proof_assets"
    single_dir = output_dir / "proof_single_slides"
    output_dir.mkdir(exist_ok=True)
    asset_dir.mkdir(exist_ok=True)
    single_dir.mkdir(exist_ok=True)

    before = asset_dir / "before-dashboard.png"
    after = asset_dir / "after-dashboard.png"
    walkthrough = asset_dir / "walkthrough-step.png"

    _draw_mock_screen(
        before, "Legacy Billing Dashboard", (217, 48, 37), "Dense layout, weak hierarchy"
    )
    _draw_mock_screen(
        after,
        "Redesigned Billing Dashboard",
        (26, 115, 232),
        "Clear KPI hierarchy and stronger CTAs",
    )
    _draw_mock_screen(
        walkthrough, "Account Settings Flow", (52, 168, 83), "Step-by-step walkthrough"
    )

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    brand = {
        "primary_color": "#1a73e8",
        "secondary_color": "#34a853",
        "font_family": "Aptos",
        "theme": "modern",
    }

    slides = [
        (
            "title",
            {
                "eyebrow": "slide_service proof deck",
                "title": "Audit findings can render as polished, image-aware slides",
                "subtitle": "This deck uses the real slide_service template renderers with local screenshot assets.",
            },
        ),
        (
            "scorecard",
            {
                "title": "Executive Summary",
                "metrics": [
                    {"label": "Performance", "value": "72"},
                    {"label": "Accessibility", "value": "84"},
                    {"label": "Clarity", "value": "+18"},
                    {"label": "Task Success", "value": "+22%"},
                ],
                "footer": "Sample values used to validate layout, spacing, and typography.",
            },
        ),
        (
            "finding_detail",
            {
                "title": "Critical billing page overloads the user with equal-weight content",
                "severity": "high",
                "description": (
                    "The current billing dashboard mixes history, alerts, payment actions, and "
                    "support content into a single view. The primary action is visually lost."
                ),
                "recommendation": (
                    "Restructure the page around top KPIs, isolate the payment CTA, and move "
                    "secondary support content into a contextual panel."
                ),
                "screenshot_url": str(before),
            },
        ),
        (
            "comparison",
            {
                "title": "Before / After Comparison",
                "before_label": "Before",
                "before_image_url": str(before),
                "after_label": "After",
                "after_image_url": str(after),
                "caption": "Images are placed as real cover-fit screenshots rather than placeholder text boxes.",
            },
        ),
        (
            "feature_spotlight",
            {
                "title": "Redesign spotlight: the billing summary becomes scannable",
                "description": "The new layout puts payment status, spend trend, and next action above the fold.",
                "image_url": str(after),
                "bullets": [
                    {"text": "Primary CTA promoted into the hero zone"},
                    {"text": "KPI grouping replaces undifferentiated tables"},
                    {"text": "Secondary actions move into compact cards"},
                    {"text": "Hierarchy is readable on first glance"},
                ],
            },
        ),
        (
            "tutorial_step",
            {
                "step_number": "1",
                "title": "Open Account Settings and review notification defaults",
                "instruction": (
                    "Navigate to Settings, confirm the active billing profile, and review the "
                    "notification matrix before enabling autopay."
                ),
                "screenshot_url": str(walkthrough),
                "tip": "Use the highlighted summary card as the starting checkpoint in onboarding docs.",
            },
        ),
        (
            "cta",
            {
                "title": "Result",
                "body": "The service can now render branded deck slides with real screenshots, corrected widescreen sizing, and more deliberate visual hierarchy.",
                "cta_text": "Ready for W2V integration",
                "cta_url": "artifact: slide_service/test_output/slide_service_proof_deck.pptx",
                "contact_info": "Next step: wire the resulting PPTX and manifest into pack delivery.",
            },
        ),
    ]

    for layout_type, content in slides:
        render_slide(prs, layout_type=layout_type, content=content, brand=brand, styling_rules={})

    output_path = output_dir / "slide_service_proof_deck.pptx"
    prs.save(str(output_path))
    print(output_path)

    for layout_type, content in slides:
        single = Presentation()
        single.slide_width = Inches(13.333)
        single.slide_height = Inches(7.5)
        render_slide(
            single,
            layout_type=layout_type,
            content=content,
            brand=brand,
            styling_rules={},
        )
        single_path = single_dir / f"{layout_type}.pptx"
        single.save(str(single_path))
        print(single_path)


if __name__ == "__main__":
    main()
