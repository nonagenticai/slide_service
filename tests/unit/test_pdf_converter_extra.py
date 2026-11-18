"""Extended tests for PPTX-to-PDF converter covering subprocess paths."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.services.pdf_converter import (
    PDFConversionError,
    _convert_pptx_to_pdf_sync,
    convert_pptx_to_pdf,
)


def _make_success_result(output_dir: Path | None = None) -> MagicMock:
    """Build a mock subprocess.CompletedProcess that signals success."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    result.stdout = ""
    return result


def test_pdf_conversion_timeout():
    """TimeoutExpired from subprocess becomes PDFConversionError."""
    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired("soffice", 120)),
        pytest.raises(PDFConversionError, match="timed out"),
    ):
        _convert_pptx_to_pdf_sync(b"fake-pptx")


def test_pdf_conversion_nonzero_exit():
    """Non-zero return code raises PDFConversionError with the error text."""
    result = MagicMock()
    result.returncode = 1
    result.stderr = "conversion failed"
    result.stdout = ""

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", return_value=result),
        pytest.raises(PDFConversionError, match="exit code 1"),
    ):
        _convert_pptx_to_pdf_sync(b"fake-pptx")


def test_pdf_conversion_nonzero_exit_stdout_fallback():
    """Non-zero return code with empty stderr falls back to stdout."""
    result = MagicMock()
    result.returncode = 2
    result.stderr = ""
    result.stdout = "some output"

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", return_value=result),
        pytest.raises(PDFConversionError, match="some output"),
    ):
        _convert_pptx_to_pdf_sync(b"fake-pptx")


def test_pdf_conversion_success(tmp_path: Path):
    """Successful conversion returns the PDF bytes written by the subprocess."""
    pdf_bytes = b"%PDF-1.7 fake"

    def _fake_run(command: list[str], **kwargs: Any) -> MagicMock:
        # Extract the output dir from the command args
        out_dir_idx = command.index("--outdir") + 1
        out_dir = Path(command[out_dir_idx])
        (out_dir / "deck.pdf").write_bytes(pdf_bytes)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        result = _convert_pptx_to_pdf_sync(b"fake-pptx-bytes")

    assert result == pdf_bytes


def test_pdf_conversion_uses_writable_libreoffice_profile():
    """soffice is invoked with a writable UserInstallation + XDG dirs.

    Guards the read-only-rootfs fix: LibreOffice must not fall back to
    $HOME for its user-installation profile or dconf/XDG caches, or it
    aborts with "User installation could not be completed".
    """
    pdf_bytes = b"%PDF-1.7 fake"
    captured: dict[str, Any] = {}

    def _fake_run(command: list[str], **kwargs: Any) -> MagicMock:
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        out_dir = Path(command[command.index("--outdir") + 1])
        (out_dir / "deck.pdf").write_bytes(pdf_bytes)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        assert _convert_pptx_to_pdf_sync(b"fake-pptx-bytes") == pdf_bytes

    profile_args = [a for a in captured["command"] if a.startswith("-env:UserInstallation=file://")]
    assert profile_args, "soffice must receive an explicit writable UserInstallation profile"
    env = captured["env"]
    assert env is not None, "subprocess must run with an explicit env"
    for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        assert key in env, f"{key} must be set to a writable path"
    # The profile and caches must live under the same per-conversion temp dir,
    # i.e. not the read-only container $HOME.
    profile_path = profile_args[0].removeprefix("-env:UserInstallation=file://")
    assert profile_path.startswith(env["HOME"]), "profile must live under the writable HOME"
    assert env["XDG_CACHE_HOME"].startswith(env["HOME"])


def test_pdf_conversion_success_alternative_pdf_name(tmp_path: Path):
    """If deck.pdf is absent but another .pdf exists, it is used instead."""
    pdf_bytes = b"%PDF-1.7 alt"

    def _fake_run(command: list[str], **kwargs: Any) -> MagicMock:
        out_dir_idx = command.index("--outdir") + 1
        out_dir = Path(command[out_dir_idx])
        # Write as a different filename
        (out_dir / "presentation.pdf").write_bytes(pdf_bytes)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        result = _convert_pptx_to_pdf_sync(b"fake-pptx-bytes")

    assert result == pdf_bytes


def test_pdf_conversion_no_pdf_produced():
    """Subprocess exits 0 but produces no PDF -> PDFConversionError."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    result.stdout = ""

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", return_value=result),
        pytest.raises(PDFConversionError, match="did not produce a PDF"),
    ):
        _convert_pptx_to_pdf_sync(b"fake-pptx")


@pytest.mark.asyncio
async def test_convert_pptx_to_pdf_async():
    """Async wrapper calls the sync function in a thread and returns bytes."""
    pdf_bytes = b"%PDF-1.7 async"

    def _fake_run(command: list[str], **kwargs: Any) -> MagicMock:
        out_dir_idx = command.index("--outdir") + 1
        out_dir = Path(command[out_dir_idx])
        (out_dir / "deck.pdf").write_bytes(pdf_bytes)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with (
        patch("shutil.which", return_value="/usr/bin/soffice"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        result = await convert_pptx_to_pdf(b"fake-pptx-async")

    assert result == pdf_bytes
