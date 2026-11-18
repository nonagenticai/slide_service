"""Tests for PPTX-to-PDF converter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.pdf_converter import PDFConversionError, _convert_pptx_to_pdf_sync


def test_pdf_conversion_soffice_not_found():
    """Test that missing soffice raises PDFConversionError."""
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(PDFConversionError, match="LibreOffice runtime not found"),
    ):
        _convert_pptx_to_pdf_sync(
            b"fake-pptx",
            soffice_binary="/nonexistent/soffice",
        )


def test_pdf_conversion_error_class():
    """Test PDFConversionError is a RuntimeError."""
    err = PDFConversionError("test error")
    assert isinstance(err, RuntimeError)
    assert str(err) == "test error"
