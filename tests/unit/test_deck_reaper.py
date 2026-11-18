"""Real-DB tests for the multi-replica stuck-deck-job reaper.

Exercises ``src.services.deck_reaper.reap_stuck_deck_jobs`` against the real postgres
CI DB (the SECURITY DEFINER resolvers ``stuck_deck_jobs`` / ``fail_stuck_deck_job``
are installed by the ``create_all`` ``after_create`` event, mirroring the alembic
migration in prod). Five properties:

(a) a ``processing`` job older than the stale threshold with NO live Redis lock is
    reaped to ``failed``;
(b) a ``processing`` job whose Redis lock is still present is NOT reaped;
(b2) when Redis is UNREACHABLE nothing is reaped — "could not ask" is not
    "unowned", and collapsing the two reaps every in-flight job at once;
(c) a fresh ``processing`` job (recent ``updated_at``) is NOT reaped;
(d) two concurrent reaper ticks — only one performs the reap (advisory lock).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from sqlalchemy import select, text

from src.cache.job_lock import job_lock_key
from src.database.models import DeckJob
from src.database.reaper_sql import REAPER_ADVISORY_LOCK_KEY
from src.services.deck_reaper import reap_stuck_deck_jobs

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient

_STALE = 900  # matches ReaperSettings default (15 min)

#: ``deck_jobs.tenant_id`` is a native ``uuid`` column, so a test tenant must be a
#: real uuid. The old ``"t1"`` literal now raises InvalidTextRepresentation.
TENANT_T1 = "0195f48b-d000-7000-8000-000000000001"


class _FakeRawRedis:
    """The raw redis client behind ``RedisClient._ensure_connected``."""

    def __init__(self, present_keys: set[str] | None = None, *, fail: bool = False) -> None:
        self._present = present_keys or set()
        self._fail = fail

    async def exists(self, key: str) -> int:
        if self._fail:
            raise RedisConnectionError("Redis unavailable")
        return 1 if key in self._present else 0


class _FakeRedis:
    """Minimal RedisClient stand-in, exposing BOTH routes to the same question.

    ``job_lock_present`` deliberately goes through ``_ensure_connected`` to the
    raw client rather than ``RedisClient.exists``, because the latter returns
    ``False`` both when the key is absent and when Redis is unreachable. The
    fake therefore offers both: the raw client, which distinguishes the two, and
    an ``exists`` that collapses them exactly as ``RedisClient.exists`` does.
    """

    def __init__(self, present_keys: set[str] | None = None, *, fail: bool = False) -> None:
        self._raw = _FakeRawRedis(present_keys, fail=fail)

    def _ensure_connected(self) -> _FakeRawRedis:
        return self._raw

    async def exists(self, key: str) -> bool:
        """``RedisClient.exists``, mirrored so a revert reds on BEHAVIOUR.

        Reverting ``job_lock_present`` to ``await redis_client.exists(key)``
        used to red these tests with ``AttributeError: '_FakeRedis' object has
        no attribute 'exists'`` — the mutation never reached the reaper's
        decision at all, so it discriminated nothing. Present here (with the
        real swallow-and-answer-False semantics) the revert instead makes the
        reaper read a held lock as absent, which is the actual defect.
        """
        try:
            return bool(await self._ensure_connected().exists(key))
        except RedisError:
            # THE COLLAPSE: "I could not ask" is returned as "the key is absent".
            return False


def _redis(present_keys: set[str] | None = None, *, fail: bool = False) -> RedisClient:
    """A fake Redis typed as RedisClient (the reaper only checks lock presence)."""
    return cast("RedisClient", _FakeRedis(present_keys, fail=fail))


async def _seed_job(db_session_factory, *, age_seconds: int, tenant_id: str = TENANT_T1) -> str:
    """Insert a processing DeckJob and force its updated_at to ``age_seconds`` ago."""
    job_id = str(uuid4())
    async with db_session_factory() as s:
        s.add(DeckJob(job_id=job_id, tenant_id=tenant_id, status="processing"))
        await s.commit()
    # Raw UPDATE so the ORM onupdate=now() does NOT fire — we control updated_at.
    async with db_session_factory() as s:
        await s.execute(
            text(
                "UPDATE deck_jobs SET updated_at = now() - make_interval(secs => :age) "
                "WHERE job_id = :jid"
            ),
            {"age": age_seconds, "jid": job_id},
        )
        await s.commit()
    return job_id


async def _status(db_session_factory, job_id: str) -> tuple[str, str | None]:
    async with db_session_factory() as s:
        job = (await s.execute(select(DeckJob).where(DeckJob.job_id == job_id))).scalar_one()
        return job.status, job.error


@pytest.mark.asyncio
async def test_stale_job_without_lock_is_reaped(db_session_factory):
    """(a) old processing job + no live lock → reaped to failed with a reason."""
    job_id = await _seed_job(db_session_factory, age_seconds=_STALE + 600)

    reaped = await reap_stuck_deck_jobs(
        db_session_factory, _redis(), stale_threshold_seconds=_STALE
    )

    assert reaped == 1
    status, error = await _status(db_session_factory, job_id)
    assert status == "failed"
    assert error and "reaped" in error


@pytest.mark.asyncio
async def test_stale_job_with_live_lock_is_not_reaped(db_session_factory):
    """(b) old processing job but Redis lock still held → skipped (primary guard)."""
    job_id = await _seed_job(db_session_factory, age_seconds=_STALE + 600)
    redis = _redis(present_keys={job_lock_key(job_id)})

    reaped = await reap_stuck_deck_jobs(db_session_factory, redis, stale_threshold_seconds=_STALE)

    assert reaped == 0
    status, _ = await _status(db_session_factory, job_id)
    assert status == "processing"


@pytest.mark.asyncio
async def test_a_redis_outage_does_not_reap_every_stale_job(db_session_factory):
    """(b2) Redis is DOWN → "cannot confirm absent" → nothing is reaped.

    (b) above cannot see this: its fake always answers, so "held" and "could not
    ask" never diverge. Here ``exists`` raises, and ``RedisClient.exists`` maps
    that to ``False`` — indistinguishable from "the key is absent". Routing the
    presence check through ``RedisClient.exists`` therefore reads EVERY
    in-flight job as unowned at the same moment and reaps the lot: a correlated
    fail-open, produced by the very guard meant to survive the outage.

    This is the test that makes reverting ``job_lock_present`` to
    ``await redis_client.exists(key)`` red on BEHAVIOUR at the reaper level
    (three jobs wrongly failed) rather than on a missing fake attribute.
    """
    job_ids = [await _seed_job(db_session_factory, age_seconds=_STALE + 600) for _ in range(3)]

    reaped = await reap_stuck_deck_jobs(
        db_session_factory, _redis(fail=True), stale_threshold_seconds=_STALE
    )

    assert reaped == 0, "an unreachable Redis is not evidence that a job is unowned"
    for job_id in job_ids:
        status, _ = await _status(db_session_factory, job_id)
        assert status == "processing"


@pytest.mark.asyncio
async def test_fresh_job_is_not_reaped(db_session_factory):
    """(c) recently-updated processing job → not a candidate, never reaped."""
    job_id = await _seed_job(db_session_factory, age_seconds=5)

    reaped = await reap_stuck_deck_jobs(
        db_session_factory, _redis(), stale_threshold_seconds=_STALE
    )

    assert reaped == 0
    status, _ = await _status(db_session_factory, job_id)
    assert status == "processing"


@pytest.mark.asyncio
async def test_advisory_lock_serialises_concurrent_ticks(db_session_factory):
    """(d) while one holder owns the advisory lock, a reaper tick no-ops; once
    released, the next tick reaps — proving only one replica acts per tick."""
    job_id = await _seed_job(db_session_factory, age_seconds=_STALE + 600)

    # Hold the reaper's advisory lock in a separate open transaction, simulating
    # another replica mid-sweep. The lock is transaction-scoped → released on rollback.
    async with db_session_factory() as holder:
        got = (
            await holder.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": REAPER_ADVISORY_LOCK_KEY},
            )
        ).scalar()
        assert got is True

        blocked = await reap_stuck_deck_jobs(
            db_session_factory, _redis(), stale_threshold_seconds=_STALE
        )
        assert blocked == 0
        status, _ = await _status(db_session_factory, job_id)
        assert status == "processing", "reaper acted despite another replica holding the lock"

        await holder.rollback()  # release the advisory lock

    # Lock free → this tick acquires it and reaps.
    reaped = await reap_stuck_deck_jobs(
        db_session_factory, _redis(), stale_threshold_seconds=_STALE
    )
    assert reaped == 1
    status, _ = await _status(db_session_factory, job_id)
    assert status == "failed"
