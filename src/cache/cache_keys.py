"""Cache key construction and input hashing for slide_service."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _default_serializer(obj: Any) -> Any:
    """Deterministic JSON serializer for datetime, UUID, and other objects."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "hex"):
        return obj.hex
    return repr(obj)


def _stable_hash(data: Any) -> str:
    """Create a SHA256-based deterministic hash of data."""
    serialized = json.dumps(data, sort_keys=True, default=_default_serializer)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def job_status_key(job_id: str) -> str:
    """Cache key for job status polling."""
    return f"slide:job:{job_id}:status"


def job_status_ttl(status: str) -> int:
    """Return TTL in seconds based on job status."""
    if status == "processing":
        return 30
    if status in ("completed", "failed"):
        return 300
    return 10


def deck_cache_key(request_payload: Any) -> str:
    """Cache key for completed deck results. TTL: 1hr."""
    return f"slide:deck:{_stable_hash(request_payload)}"


DECK_CACHE_TTL = 3600
