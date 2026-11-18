"""Distributed job locking via Redis SET NX to prevent duplicate processing."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable
from typing import TYPE_CHECKING, cast

from redis.exceptions import RedisError

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "slide:lock:job:"
_OWNER_ID = uuid.uuid7().hex[-12:]

# Lua script: release only if this pod owns the lock
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def job_lock_key(job_id: str) -> str:
    """Return the Redis key holding the distributed lock for ``job_id``."""
    return f"{_LOCK_PREFIX}{job_id}"


async def job_lock_present(redis_client: RedisClient | None, job_id: str) -> bool:
    """Report whether a live owner still holds the job lock (reaper guard).

    Conservative by design: returns ``True`` (treat as held → do NOT reap) whenever
    we cannot positively confirm the key is absent. Only a positive "key does not
    exist" answer returns ``False``. This makes lock-presence the reaper's PRIMARY
    guard: a job whose owner is still alive holds the key, so the reaper skips it
    even if its updated_at looks stale. (The stale ``updated_at`` threshold, set far
    above the lock TTL and heartbeat interval, is the second, independent guard.)

    ⚠️ The docstring above USED TO BE FALSE, and this function is where it was
    made true. It delegated to ``RedisClient.exists``, which returns ``False`` on
    a ``RedisError`` *and* on the not-connected ``RuntimeError`` — it collapses
    "the key is absent" and "I could not ask" into the same answer. So during a
    Redis outage the reaper read "no live owner" for EVERY job at once: exactly
    the correlated failure the guard exists to survive, and the moment it turned
    into a fail-open. A safety gate that claims a property it does not have is
    worse than one that admits it cannot decide, so the CODE was changed to match
    the docstring rather than the docstring softened to match the code.

    It therefore talks to the raw client directly, like ``acquire_job_lock`` and
    ``release_job_lock`` next door and for the same reason: only there is the
    error distinguishable from a real answer. Any error means "cannot confirm
    absent" → held. The blast radius of the conservative answer is bounded — a
    genuinely dead job is still reaped once Redis returns, and the 900s staleness
    threshold plus the heartbeat remain the independent second guard — so it
    needs nothing more elaborate than this.
    """
    if redis_client is None:
        return True
    key = job_lock_key(job_id)
    try:
        client = redis_client._ensure_connected()
        return bool(await client.exists(key))
    except (OSError, RuntimeError, RedisError) as exc:
        logger.warning(
            "Redis lock-presence check failed for job %s: %s. Cannot confirm the key "
            "is ABSENT, so treating the job as still lock-held (not reaped).",
            job_id,
            exc,
        )
        return True


async def acquire_job_lock(redis_client: RedisClient | None, job_id: str, ttl: int = 300) -> bool:
    """Attempt to acquire a distributed lock for a job.

    Fail-open: returns True if Redis is unavailable (allows processing).
    """
    if redis_client is None:
        return True
    try:
        client = redis_client._ensure_connected()
        key = f"{_LOCK_PREFIX}{job_id}"
        result = await client.set(key, _OWNER_ID, nx=True, ex=ttl)
        if result:
            logger.debug("Acquired lock for job %s", job_id)
            return True
        logger.info("Job %s already locked by another pod", job_id)
        return False
    except (OSError, RuntimeError, RedisError) as exc:
        # These helpers call the RAW redis client directly (not RedisClient.get/
        # set, which swallow RedisError), so a redis ConnectionError/TimeoutError
        # -- a RedisError(Exception), NOT an OSError -- surfaces here. acquire is
        # awaited before the deck task's own try/except, so an uncaught RedisError
        # would wedge the job at "processing". Honour the documented fail-open.
        logger.warning("Redis lock acquisition failed for job %s: %s (fail-open)", job_id, exc)
        return True


async def release_job_lock(redis_client: RedisClient | None, job_id: str) -> None:
    """Release a distributed lock for a job.

    Uses a Lua script to ensure only the lock owner can release it.
    """
    if redis_client is None:
        return
    try:
        client = redis_client._ensure_connected()
        key = f"{_LOCK_PREFIX}{job_id}"
        await cast(Awaitable[str], client.eval(_RELEASE_SCRIPT, 1, key, _OWNER_ID))
        logger.debug("Released lock for job %s", job_id)
    except (OSError, RuntimeError, RedisError) as exc:
        logger.warning("Redis lock release failed for job %s: %s", job_id, exc)
