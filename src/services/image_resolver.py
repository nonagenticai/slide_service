"""Resolve image references from URLs, data URLs, and storage paths."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from w2v_common.net import ResponseTooLargeError, SSRFError, safe_get_sync

from src.config import get_settings
from src.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Everything a remote image fetch may raise that must degrade to the placeholder
# rather than abort the deck. Named because it does not fit on one line; the
# membership is what matters, not the parenthesised form.
#
# ``httpx.InvalidURL`` is deliberately listed: it derives straight from
# ``Exception``, NOT from ``httpx.HTTPError``, so it is not covered by the
# transport-error entry. ``ValueError``/``TypeError`` cover url-construction
# rejections raised before a request ever leaves the process.
_IMAGE_FETCH_ERRORS = (
    httpx.HTTPError,
    httpx.InvalidURL,
    OSError,
    SSRFError,
    ResponseTooLargeError,
    ValueError,
    TypeError,
)


def _endpoint_netloc(endpoint: str | None) -> str | None:
    """Normalize a MinIO endpoint setting to a bare ``host[:port]`` netloc."""
    if not endpoint:
        return None
    ep = endpoint.strip()
    if "://" in ep:
        ep = urlparse(ep).netloc or ep.split("://", 1)[1]
    ep = ep.split("/", 1)[0]  # drop any path
    if "@" in ep:
        ep = ep.rsplit("@", 1)[-1]  # drop any userinfo
    return ep.lower() or None


def _minio_allow_hosts() -> set[str]:
    """Our own MinIO endpoint netlocs — presigned URLs at these internal hosts
    are trusted past the SSRF guard (they legitimately resolve into a blocked
    internal range). Everything else, including redirects, is still validated."""
    storage = get_settings().storage
    hosts: set[str] = set()
    for ep in (storage.minio_endpoint, getattr(storage, "minio_external_endpoint", None)):
        netloc = _endpoint_netloc(ep)
        if netloc:
            hosts.add(netloc)
    return hosts


def _storage_candidates(image_ref: str) -> list[Path]:
    parsed = urlparse(image_ref)
    if parsed.scheme == "artifact":
        relative = (parsed.netloc + parsed.path).lstrip("/")
        if not relative:
            return []
        try:
            storage = StorageService()
            return [Path(storage.get_full_path(relative))]
        except OSError, RuntimeError:
            logger.warning("Failed to resolve artifact image path: %s", image_ref, exc_info=True)
            return []

    if parsed.scheme == "file":
        return [Path(unquote(parsed.path))]

    path = Path(image_ref)
    candidates = [path]
    if not path.is_absolute():
        try:
            storage = StorageService()
            candidates.append(Path(storage.get_full_path(image_ref)))
        except OSError, RuntimeError:
            logger.debug("Storage candidate unavailable for %s", image_ref, exc_info=True)
    return candidates


def _decode_data_url(image_ref: str) -> bytes | None:
    try:
        header, payload = image_ref.split(",", 1)
        if ";base64" not in header:
            return None
        return base64.b64decode(payload)
    except OSError, ValueError:
        logger.warning("Invalid data URL image payload", exc_info=True)
        return None


def _read_local_candidates(image_ref: str) -> bytes | None:
    for candidate in _storage_candidates(image_ref):
        try:
            if candidate.is_file():
                return candidate.read_bytes()
        except OSError, RuntimeError:
            logger.warning("Failed to read image file: %s", candidate, exc_info=True)
    return None


def load_image_bytes(image_ref: str | None) -> bytes | None:
    """Load image bytes from supported references.

    Uses a context-managed ``httpx.Client`` for HTTP fetches. Supported inputs:

    - ``data:`` URLs
    - ``http(s)`` URLs, including presigned MinIO URLs
    - ``file:`` URLs
    - ``artifact:`` storage-relative paths
    - Plain filesystem paths

    Callers in an async context should invoke this from a worker thread via
    ``asyncio.to_thread`` to avoid blocking the event loop.
    """

    image_ref = str(image_ref or "").strip()
    if not image_ref:
        return None

    if image_ref.startswith("data:"):
        return _decode_data_url(image_ref)

    try:
        parsed = urlparse(image_ref)
    except ValueError:
        # ``urlparse`` itself rejects some malformed refs BEFORE any fetch is
        # attempted (e.g. the unterminated IPv6 literal "http://[::1" raises
        # "Invalid IPv6 URL"). This call used to sit outside any try-block, so
        # a single bad ref propagated all the way out of the deck render loop.
        logger.warning("Unparseable image reference: %s", image_ref, exc_info=True)
        return None

    if parsed.scheme in {"http", "https"}:
        try:
            with httpx.Client() as client:
                # SSRF-safe: per-hop redirect re-validation, with our own MinIO
                # hosts allow-listed so presigned internal URLs still fetch.
                response = safe_get_sync(
                    client,
                    image_ref,
                    allow_hosts=_minio_allow_hosts(),
                    timeout=20.0,
                    max_fetch_bytes=get_settings().render.max_image_fetch_bytes,
                )
                response.raise_for_status()
                return response.content
        except _IMAGE_FETCH_ERRORS:
            logger.warning("Failed to fetch remote image: %s", image_ref, exc_info=True)
            return None

    return _read_local_candidates(image_ref)
