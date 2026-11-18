"""Extra tests for cache_keys covering _default_serializer and _stable_hash."""

from __future__ import annotations

import datetime
import uuid

from src.cache.cache_keys import _default_serializer, _stable_hash


def test_default_serializer_datetime():
    """datetime objects are serialized via .isoformat()."""
    dt = datetime.datetime(2024, 1, 15, 12, 30, 0)
    result = _default_serializer(dt)
    assert result == dt.isoformat()


def test_default_serializer_date():
    """date objects are serialized via .isoformat()."""
    d = datetime.date(2024, 1, 15)
    result = _default_serializer(d)
    assert result == d.isoformat()


def test_default_serializer_uuid():
    """UUID objects are serialized via .hex."""
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    result = _default_serializer(uid)
    assert result == uid.hex


def test_default_serializer_fallback():
    """Unknown types fall back to repr()."""

    class _Custom:
        def __repr__(self) -> str:
            return "custom-repr"

    result = _default_serializer(_Custom())
    assert result == "custom-repr"


def test_stable_hash_deterministic():
    """Same input always produces the same hash."""
    data = {"key": "value", "num": 42}
    assert _stable_hash(data) == _stable_hash(data)


def test_stable_hash_different_inputs():
    """Different inputs produce different hashes."""
    assert _stable_hash({"a": 1}) != _stable_hash({"a": 2})


def test_stable_hash_length():
    """Hash is 16 hex characters."""
    result = _stable_hash({"title": "Test"})
    assert len(result) == 16
    assert all(c in "0123456789abcdef" for c in result)


def test_stable_hash_with_datetime():
    """Hash works with datetime objects via _default_serializer."""
    dt = datetime.datetime(2024, 6, 1, 0, 0, 0)
    result = _stable_hash({"ts": dt})
    assert isinstance(result, str)
    assert len(result) == 16
