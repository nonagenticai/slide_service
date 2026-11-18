"""PPTX-to-PDF conversion using a headless LibreOffice runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess  # nosec B404
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)


class PDFConversionError(RuntimeError):
    """Raised when PPTX-to-PDF conversion fails."""


def _convert_pptx_to_pdf_sync(
    pptx_bytes: bytes,
    *,
    soffice_binary: str = "soffice",
    timeout_seconds: int = 120,
) -> bytes:
    soffice_path = shutil.which(soffice_binary) or soffice_binary

    with TemporaryDirectory(prefix="slide-service-pdf-") as temp_dir:
        workdir = Path(temp_dir)
        input_path = workdir / "deck.pptx"
        output_dir = workdir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(pptx_bytes)

        # The container runs with a read-only root filesystem, so the default
        # LibreOffice user-installation profile (under $HOME) and the dconf/XDG
        # caches cannot be created and soffice aborts with "User installation
        # could not be completed". Point every writable LibreOffice/XDG path at
        # this per-conversion temp dir (which lives on the writable /tmp mount)
        # and pass the profile via ``-env:UserInstallation``.
        lo_profile = workdir / "lo-profile"
        cache_home = workdir / "cache"
        config_home = workdir / "config"
        for d in (lo_profile, cache_home, config_home):
            d.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "HOME": str(workdir),
            "XDG_CACHE_HOME": str(cache_home),
            "XDG_CONFIG_HOME": str(config_home),
        }

        command = [
            soffice_path,
            f"-env:UserInstallation=file://{lo_profile}",
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(input_path),
        ]

        try:
            result = subprocess.run(  # nosec B603  # noqa: S603 -- controlled internal command, no user input
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )
        except FileNotFoundError as exc:
            raise PDFConversionError(
                "LibreOffice runtime not found. Ensure 'soffice' is installed and on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PDFConversionError(
                f"PPTX-to-PDF conversion timed out after {timeout_seconds}s."
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            raise PDFConversionError(
                f"PPTX-to-PDF conversion failed with exit code {result.returncode}: "
                f"{stderr or stdout or 'no output'}"
            )

        pdf_path = output_dir / "deck.pdf"
        if not pdf_path.exists():
            generated = sorted(output_dir.glob("*.pdf"))
            if generated:
                pdf_path = generated[0]
        if not pdf_path.exists():
            raise PDFConversionError("LibreOffice reported success but did not produce a PDF.")

        pdf_bytes = pdf_path.read_bytes()
        logger.info("Converted PPTX to PDF (%d bytes)", len(pdf_bytes))
        return pdf_bytes


async def convert_pptx_to_pdf(
    pptx_bytes: bytes,
    *,
    soffice_binary: str = "soffice",
    timeout_seconds: int = 120,
) -> bytes:
    """Convert PPTX bytes into PDF bytes without blocking the event loop."""
    return await asyncio.to_thread(
        _convert_pptx_to_pdf_sync,
        pptx_bytes,
        soffice_binary=soffice_binary,
        timeout_seconds=timeout_seconds,
    )
