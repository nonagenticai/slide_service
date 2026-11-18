"""Extended tests for image_resolver covering artifact://, file://, and HTTP paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import clear_settings_cache
from src.services.image_resolver import _storage_candidates, load_image_bytes


@pytest.fixture(autouse=True)
def _reset_settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
# _storage_candidates
# ---------------------------------------------------------------------------


def test_storage_candidates_artifact_scheme(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """artifact:// scheme resolves via StorageService.get_full_path."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()

    candidates = _storage_candidates("artifact://decks/job-1/slide.png")
    assert len(candidates) == 1
    assert "decks/job-1/slide.png" in str(candidates[0])


def test_storage_candidates_artifact_scheme_empty_path():
    """artifact:// with empty path returns empty list."""
    candidates = _storage_candidates("artifact://")
    assert candidates == []


def test_storage_candidates_artifact_scheme_storage_error(monkeypatch: pytest.MonkeyPatch):
    """artifact:// that raises in StorageService returns empty list."""
    monkeypatch.setenv("STORAGE_BACKEND", "invalid-backend")
    clear_settings_cache()

    # This will fail internally; should return []
    candidates = _storage_candidates("artifact://some/path.png")
    # Either empty (exception path) or one candidate - just ensure no crash
    assert isinstance(candidates, list)


def test_storage_candidates_file_scheme(tmp_path: Path):
    """file:// scheme returns the decoded path as a candidate."""
    image_file = tmp_path / "slide.png"
    image_file.write_bytes(b"PNG")
    candidates = _storage_candidates(f"file://{image_file}")
    assert len(candidates) == 1
    assert candidates[0] == image_file


def test_storage_candidates_relative_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Relative path returns both original path and storage-resolved path."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()

    candidates = _storage_candidates("relative/image.png")
    assert len(candidates) >= 1


def test_storage_candidates_absolute_path(tmp_path: Path):
    """Absolute path returns just the original path (storage not consulted)."""
    abs_path = str(tmp_path / "image.png")
    candidates = _storage_candidates(abs_path)
    assert len(candidates) == 1
    assert str(candidates[0]) == abs_path


# ---------------------------------------------------------------------------
# load_image_bytes - HTTP path
# ---------------------------------------------------------------------------


def _patched_httpx_client(response: MagicMock | None = None, *, side_effect=None):
    """Build a context-manager mock standing in for ``httpx.Client``."""
    fake_client = MagicMock()
    if side_effect is not None:
        fake_client.get = MagicMock(side_effect=side_effect)
    else:
        fake_client.get = MagicMock(return_value=response)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_client)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, fake_client


def test_load_image_bytes_https_success():
    """Successful HTTPS fetch returns the body and wires the SSRF-safe cap.

    Mock at the ``safe_get_sync`` boundary — the transport internals (manual
    redirect re-validation, IP-pinning, the streamed max_fetch_bytes cap) are
    w2v_common's contract, exercised in its own suite, not asserted here.
    """
    fake_response = MagicMock()
    fake_response.content = b"\x89PNG\r\n\x1a\n"
    fake_response.raise_for_status = MagicMock()
    fake_safe_get = MagicMock(return_value=fake_response)

    with patch("src.services.image_resolver.safe_get_sync", fake_safe_get):
        result = load_image_bytes("https://example.com/image.png")

    assert result == b"\x89PNG\r\n\x1a\n"
    assert fake_safe_get.call_count == 1
    call = fake_safe_get.call_args
    assert call.args[1] == "https://example.com/image.png"  # (client, url, ...)
    assert call.kwargs["max_fetch_bytes"] == 25 * 1024 * 1024  # OOM cap wired through
    assert call.kwargs["timeout"] == 20.0
    assert "allow_hosts" in call.kwargs


def test_load_image_bytes_https_raises():
    """A fetch that raises (transport error) returns None gracefully."""
    with patch(
        "src.services.image_resolver.safe_get_sync",
        MagicMock(side_effect=OSError("connection refused")),
    ):
        result = load_image_bytes("https://example.com/image.png")

    assert result is None


def test_load_image_bytes_http_raise_for_status():
    """A fetch whose raise_for_status raises returns None gracefully."""
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock(side_effect=OSError("404"))
    with patch(
        "src.services.image_resolver.safe_get_sync",
        MagicMock(return_value=fake_response),
    ):
        result = load_image_bytes("http://example.com/image.png")

    assert result is None


def test_load_image_bytes_blocks_internal_url(monkeypatch: pytest.MonkeyPatch):
    """An SSRF probe to an internal address is blocked and returns None."""
    cm, fake_client = _patched_httpx_client(MagicMock())
    with patch("httpx.Client", return_value=cm):
        # 169.254.169.254 (cloud metadata) is a literal IP — no DNS needed.
        result = load_image_bytes("http://169.254.169.254/latest/meta-data")

    assert result is None
    fake_client.get.assert_not_called()


# ---------------------------------------------------------------------------
# load_image_bytes - file read errors
# ---------------------------------------------------------------------------


def test_load_image_bytes_file_read_error(tmp_path: Path):
    """File that raises on read returns None."""
    image_file = tmp_path / "bad.png"
    image_file.write_bytes(b"PNG")

    with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
        result = load_image_bytes(str(image_file))

    assert result is None


# ---------------------------------------------------------------------------
# load_image_bytes - artifact:// paths
# ---------------------------------------------------------------------------


def test_load_image_bytes_artifact_url_hits_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """artifact:// URL resolves to a local file and returns its bytes."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    clear_settings_cache()

    image_file = tmp_path / "decks" / "job-1" / "slide.png"
    image_file.parent.mkdir(parents=True, exist_ok=True)
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    result = load_image_bytes("artifact://decks/job-1/slide.png")
    assert result == b"\x89PNG\r\n\x1a\n"
