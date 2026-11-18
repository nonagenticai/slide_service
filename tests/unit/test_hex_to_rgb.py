"""Regression tests for tolerant brand-color parsing.

A single malformed brand color (empty string, a CSS name like ``red``, wrong
length, non-hex) must NOT abort the whole PPTX deck. Before the fix,
``hex_to_rgb`` did a bare ``int(hex[i:i+2], 16)`` that raised ``ValueError`` on
such input, and every renderer routes through ``_get_brand_colors`` — so one
bad color failed the entire job. The HTML render path was already tolerant, so
the PPTX path was the asymmetric failure.
"""

from __future__ import annotations

import pytest
from pptx.dml.color import RGBColor

from src.services.templates.base import BaseTemplateRenderer, hex_to_rgb


class _Renderer(BaseTemplateRenderer):
    def render(self, prs, content, brand, styling_rules):  # pragma: no cover - unused
        raise NotImplementedError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#1a73e8", RGBColor(0x1A, 0x73, 0xE8)),
        ("1a73e8", RGBColor(0x1A, 0x73, 0xE8)),  # no leading '#'
        ("#FFF", RGBColor(0xFF, 0xFF, 0xFF)),  # 3-digit shorthand
        ("#0b1f3a", RGBColor(0x0B, 0x1F, 0x3A)),
        ("  #34a853  ", RGBColor(0x34, 0xA8, 0x53)),  # surrounding whitespace
    ],
)
def test_valid_hex_parses(value, expected):
    assert hex_to_rgb(value) == expected


@pytest.mark.parametrize("bad", ["red", "", "#12", "#gggggg", "not-a-color", "#1a73e"])
def test_malformed_hex_falls_back_to_default_without_raising(bad):
    # Must not raise, and must return the supplied default color.
    assert hex_to_rgb(bad, default="#123456") == RGBColor(0x12, 0x34, 0x56)


def test_malformed_default_uses_last_resort_neutral():
    # Even a broken *default* must not raise — last-resort neutral is returned.
    assert hex_to_rgb("also-bad", default="also-bad") == RGBColor(0x1A, 0x73, 0xE8)


def test_get_brand_colors_tolerates_one_bad_color():
    r = _Renderer()
    # A bad primary must fall back to the primary default, not abort the deck;
    # the valid secondary is unaffected.
    primary, secondary = r._get_brand_colors({"primary_color": "red", "secondary_color": "#34a853"})
    assert primary == RGBColor(0x1A, 0x73, 0xE8)  # primary default
    assert secondary == RGBColor(0x34, 0xA8, 0x53)


def test_get_brand_colors_all_valid_unchanged():
    r = _Renderer()
    primary, secondary = r._get_brand_colors(
        {"primary_color": "#112233", "secondary_color": "#445566"}
    )
    assert primary == RGBColor(0x11, 0x22, 0x33)
    assert secondary == RGBColor(0x44, 0x55, 0x66)
