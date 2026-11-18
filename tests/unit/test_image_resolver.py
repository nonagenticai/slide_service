"""Tests for image reference resolution."""

from __future__ import annotations

import base64

import pytest

from src.config import clear_settings_cache
from src.services.image_resolver import load_image_bytes


@pytest.fixture(autouse=True)
def _reset_settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_load_image_bytes_empty_ref():
    """Empty reference returns None."""
    assert load_image_bytes("") is None
    assert load_image_bytes(None) is None


def test_load_image_bytes_data_url():
    """Data URL with base64 payload returns decoded bytes."""
    payload = base64.b64encode(b"PNG_FAKE").decode("ascii")
    data_url = f"data:image/png;base64,{payload}"
    result = load_image_bytes(data_url)
    assert result == b"PNG_FAKE"


def test_load_image_bytes_data_url_no_base64():
    """Data URL without base64 encoding returns None."""
    result = load_image_bytes("data:image/png,raw-content")
    assert result is None


def test_load_image_bytes_data_url_invalid():
    """Malformed data URL returns None."""
    result = load_image_bytes("data:image/png;base64,!!!invalid!!!")
    assert result is None


def test_load_image_bytes_http_url_fails_gracefully():
    """HTTP URL that fails returns None (no real server)."""
    result = load_image_bytes("http://192.0.2.1:1/no-such-image.png")
    assert result is None


def test_load_image_bytes_oversized_fetch_degrades_to_none(monkeypatch):
    """An oversized image fetch (ResponseTooLargeError) degrades to None, not a crash."""
    from w2v_common.net import ResponseTooLargeError

    def _raise_too_large(*_args, **_kwargs):
        raise ResponseTooLargeError("too big")

    monkeypatch.setattr("src.services.image_resolver.safe_get_sync", _raise_too_large)
    result = load_image_bytes("https://example.com/huge-image.png")
    assert result is None


@pytest.mark.parametrize("exc_type", [ValueError, TypeError])
def test_load_image_bytes_malformed_ref_degrades_to_none(monkeypatch, exc_type):
    """A malformed image ref that makes the HTTP fetch raise ValueError/TypeError
    must fall back to a placeholder (None), not propagate and fail the whole deck."""

    def _raise(*_args, **_kwargs):
        raise exc_type("malformed image ref")

    monkeypatch.setattr("src.services.image_resolver.safe_get_sync", _raise)
    result = load_image_bytes("https://example.com/malformed-image.png")
    assert result is None


def test_load_image_bytes_local_file(tmp_path):
    """Local file path returns file bytes."""
    image_file = tmp_path / "test.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = load_image_bytes(str(image_file))
    assert result == b"\x89PNG\r\n\x1a\n"


def test_load_image_bytes_missing_local_file():
    """Non-existent local file returns None."""
    result = load_image_bytes("/tmp/nonexistent-slide-service-test-image.png")
    assert result is None


def test_load_image_bytes_file_url(tmp_path):
    """file:// URL returns file bytes."""
    image_file = tmp_path / "test.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = load_image_bytes(f"file://{image_file}")
    assert result == b"\x89PNG\r\n\x1a\n"
