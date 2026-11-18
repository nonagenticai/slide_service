"""Tests for cache key construction."""

from __future__ import annotations

from src.cache.cache_keys import (
    DECK_CACHE_TTL,
    deck_cache_key,
    job_status_key,
    job_status_ttl,
)


def test_job_status_key():
    """Test job status key format."""
    key = job_status_key("abc-123")
    assert key == "slide:job:abc-123:status"


def test_job_status_ttl_processing():
    """Test TTL for processing jobs."""
    assert job_status_ttl("processing") == 30


def test_job_status_ttl_completed():
    """Test TTL for completed jobs."""
    assert job_status_ttl("completed") == 300


def test_job_status_ttl_failed():
    """Test TTL for failed jobs."""
    assert job_status_ttl("failed") == 300


def test_job_status_ttl_unknown():
    """Test TTL for unknown status."""
    assert job_status_ttl("pending") == 10


def test_deck_cache_key_deterministic():
    """Test deck cache key is deterministic for same input."""
    payload = {"title": "Test", "slides": []}
    key1 = deck_cache_key(payload)
    key2 = deck_cache_key(payload)
    assert key1 == key2
    assert key1.startswith("slide:deck:")


def test_deck_cache_key_different_inputs():
    """Test deck cache key differs for different inputs."""
    key1 = deck_cache_key({"title": "A"})
    key2 = deck_cache_key({"title": "B"})
    assert key1 != key2


def test_deck_cache_ttl():
    """Test deck cache TTL constant."""
    assert DECK_CACHE_TTL == 3600
