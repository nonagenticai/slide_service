"""Tests for Redis job lock acquire/release."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from src.cache.job_lock import acquire_job_lock, job_lock_present, release_job_lock

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient


class _FakeRedisClient:
    """Minimal fake that mimics the raw redis client behind RedisClient._ensure_connected."""

    def __init__(self, *, fail: bool = False, locked: bool = False) -> None:
        self._fail = fail
        self._locked = locked
        self._store: dict[str, str] = {}
        self._eval_calls: list[tuple[str, int, tuple[object, ...]]] = []

    async def set(self, key: str, value: str, *, nx: bool = True, ex: int = 300) -> bool | None:
        if self._fail:
            raise RedisConnectionError("Redis unavailable")
        if self._locked or key in self._store:
            return None
        self._store[key] = value
        return True

    async def eval(self, script: str, num_keys: int, *args: object) -> int:
        if self._fail:
            raise RedisConnectionError("Redis unavailable")
        self._eval_calls.append((script, num_keys, args))
        return 1

    async def exists(self, key: str) -> int:
        if self._fail:
            raise RedisConnectionError("Redis unavailable")
        return 1 if key in self._store else 0


class _FakeRedisWrapper:
    """Wraps _FakeRedisClient so that _ensure_connected() returns the inner client.

    It also implements ``exists`` with ``RedisClient.exists``'s REAL semantics —
    swallow ``RuntimeError``/``RedisError`` and answer ``False`` — because that
    collapse is the bug ``job_lock_present`` exists to avoid, and a fake that
    simply LACKS the method cannot express it. Without this, reverting
    ``job_lock_present`` to ``await redis_client.exists(key)`` reds these tests
    with ``AttributeError: '_FakeRedisWrapper' object has no attribute
    'exists'`` — a harness artifact that reds the outage cases and their
    controls alike, so the mutation proves nothing about behaviour. With it, the
    revert reds exactly the two outage cases (which then read ``False`` = "not
    held") and leaves both controls green, which is a real discriminator.
    """

    def __init__(self, client: _FakeRedisClient) -> None:
        self._client = client

    def _ensure_connected(self) -> _FakeRedisClient:
        return self._client

    async def exists(self, key: str) -> bool:
        """Mirror ``w2v_common.cache.redis_client.RedisClient.exists`` exactly."""
        try:
            client = self._ensure_connected()
        except RuntimeError:
            return False
        try:
            return bool(await client.exists(key))
        except RedisError:
            return False


def _wrap(client: _FakeRedisClient) -> RedisClient:
    """Create a _FakeRedisWrapper and cast it to RedisClient for type safety."""
    return cast("RedisClient", _FakeRedisWrapper(client))


@pytest.mark.asyncio
async def test_acquire_lock_no_redis():
    """Fail-open: returns True when redis is None."""
    result = await acquire_job_lock(None, "job-1")
    assert result is True


@pytest.mark.asyncio
async def test_acquire_lock_success():
    """Test successful lock acquisition."""
    client = _FakeRedisClient()
    result = await acquire_job_lock(_wrap(client), "job-1")
    assert result is True


@pytest.mark.asyncio
async def test_acquire_lock_already_locked():
    """Test lock acquisition when already locked by another pod."""
    client = _FakeRedisClient(locked=True)
    result = await acquire_job_lock(_wrap(client), "job-1")
    assert result is False


@pytest.mark.asyncio
async def test_acquire_lock_redis_error():
    """Fail-open: returns True when redis errors."""
    client = _FakeRedisClient(fail=True)
    result = await acquire_job_lock(_wrap(client), "job-1")
    assert result is True


@pytest.mark.asyncio
async def test_release_lock_no_redis():
    """Release is a no-op when redis is None."""
    await release_job_lock(None, "job-1")


@pytest.mark.asyncio
async def test_release_lock_success():
    """Test successful lock release."""
    client = _FakeRedisClient()
    await release_job_lock(_wrap(client), "job-1")
    assert len(client._eval_calls) == 1


@pytest.mark.asyncio
async def test_release_lock_redis_error():
    """Release swallows redis errors."""
    client = _FakeRedisClient(fail=True)
    # Should not raise
    await release_job_lock(_wrap(client), "job-1")


class _NotConnectedWrapper:
    """RedisClient stand-in that is wired but has never connected.

    ``RedisClient._ensure_connected`` raises ``RuntimeError`` in this state, and
    ``RedisClient.exists`` swallows it and answers ``False`` — indistinguishable
    from "the key is absent". That collapse is the bug under test.
    """

    def _ensure_connected(self) -> _FakeRedisClient:
        raise RuntimeError("RedisClient not connected. Call connect() first.")

    async def exists(self, key: str) -> bool:
        """``RedisClient.exists`` swallows the not-connected RuntimeError."""
        try:
            self._ensure_connected()
        except RuntimeError:
            return False
        raise AssertionError("unreachable: _ensure_connected always raises here")


@pytest.mark.asyncio
async def test_lock_present_when_no_redis_client():
    """No Redis wired → cannot confirm absence → treat as held."""
    assert await job_lock_present(None, "job-1") is True


@pytest.mark.asyncio
async def test_lock_absent_only_on_a_positive_answer():
    """A live Redis that positively says the key is gone is the ONLY False.

    The control for the two tests below: without it, "always True" would pass
    them both and the reaper would never reap anything.
    """
    client = _FakeRedisClient()
    assert await job_lock_present(_wrap(client), "job-1") is False


@pytest.mark.asyncio
async def test_lock_present_when_the_key_is_actually_held():
    client = _FakeRedisClient()
    await acquire_job_lock(_wrap(client), "job-1")
    assert await job_lock_present(_wrap(client), "job-1") is True


@pytest.mark.asyncio
async def test_redis_outage_does_not_report_the_lock_as_absent():
    """A Redis outage must never read as "no live owner" (would reap everything).

    Pre-fix this returned False: ``RedisClient.exists`` maps ``RedisError`` to
    ``False``, so during an outage the reaper saw every in-flight job as
    unowned at once — the correlated failure the guard exists to survive.
    """
    client = _FakeRedisClient(fail=True)
    assert await job_lock_present(_wrap(client), "job-1") is True


@pytest.mark.asyncio
async def test_unconnected_redis_does_not_report_the_lock_as_absent():
    """Same false claim by the other route: the not-connected ``RuntimeError``."""
    unconnected = cast("RedisClient", _NotConnectedWrapper())
    assert await job_lock_present(unconnected, "job-1") is True
