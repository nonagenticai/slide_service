"""Stuck-deck-job reaper — multi-replica-safe orphan cleanup.

slide_service is otherwise multi-replica-safe: ``_process_deck`` runs fire-and-forget
under a Redis SET-NX lock (``slide:lock:job:<id>``, 300s TTL) so two pods never render
the same deck. The one hole is a pod that dies mid-render: the lock TTL-expires but the
``DeckJob`` row stays wedged at ``status='processing'`` forever — nothing re-drives or
fails it.

This module closes that hole with a lease-gated periodic reaper (mirrors
``w2v_orchestrator``'s orphan reconciler). Every ``interval_seconds``, under a brief
``pg_try_advisory_xact_lock`` so only ONE replica acts per tick, it finds
``processing`` jobs whose ``updated_at`` is older than ``stale_threshold_seconds``
AND whose Redis lock is absent (no live owner), and flips them to ``failed``.

Two independent guards keep a genuinely-running job safe:

* **Lock presence (primary).** A live ``_process_deck`` holds the Redis lock; the
  reaper skips any job whose lock key is still present (or unverifiable).
* **Staleness (secondary).** ``stale_threshold_seconds`` (default 900s) is far above
  both the 300s lock TTL and the ~60s ``updated_at`` heartbeat, so a slow-but-live
  job — which heartbeats its ``updated_at`` — never looks stale, even after its lock
  TTL lapses on a >5-min render.

The cross-tenant scan + failing UPDATE go through the ``stuck_deck_jobs`` /
``fail_stuck_deck_job`` ``SECURITY DEFINER`` resolvers (see ``src.database.reaper_sql``)
because the runtime connects as the restricted ``w2v_slide_app`` role under
``TENANT_RLS_MODE=enforce`` — a plain query would fail-closed to zero rows.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.cache.job_lock import job_lock_present
from src.database.reaper_sql import REAPER_ADVISORY_LOCK_KEY

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

_REAP_REASON = "reaped: pod died mid-processing (stale >{age}s, no live lock owner)"


async def reap_stuck_deck_jobs(
    session_factory: Any,
    redis: RedisClient | None,
    *,
    stale_threshold_seconds: int,
) -> int:
    """Fail every orphaned ``processing`` deck job in one lease-gated sweep.

    Returns the number of jobs actually reaped (0 if another replica holds the
    advisory lock this tick, or if there is nothing to reap).
    """
    now = datetime.datetime.now(datetime.UTC)
    cutoff = now - datetime.timedelta(seconds=stale_threshold_seconds)

    async with session_factory() as session:
        # Brief transaction-scoped mutex so only ONE replica reaps per tick; the
        # others find the lock taken and no-op. Auto-released on commit below.
        locked = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": REAPER_ADVISORY_LOCK_KEY},
            )
        ).scalar()
        if not locked:
            logger.debug("reaper: another replica holds the lock; skipping this tick")
            return 0

        # Cross-tenant scan via the SECURITY DEFINER resolver (bypasses RLS so the
        # background session — GUC unset — still sees every tenant's orphans).
        rows = (
            await session.execute(
                text(
                    "SELECT job_id, tenant_id, updated_at "
                    "FROM stuck_deck_jobs(:cutoff) ORDER BY updated_at"
                ),
                {"cutoff": cutoff},
            )
        ).all()
        if not rows:
            await session.commit()
            return 0

        reaped = 0
        for row in rows:
            # PRIMARY guard: a live owner still holding the Redis lock is never reaped.
            if await job_lock_present(redis, row.job_id):
                logger.debug("reaper: job %s still lock-held; skipping", row.job_id)
                continue
            age = int((now - row.updated_at).total_seconds()) if row.updated_at else 0
            affected = (
                await session.execute(
                    text("SELECT fail_stuck_deck_job(:jid, :reason)"),
                    {"jid": row.job_id, "reason": _REAP_REASON.format(age=age)},
                )
            ).scalar()
            if affected:
                reaped += 1
                logger.warning(
                    "reaper: failed stuck deck job %s (tenant=%s, stale %ds, no lock owner)",
                    row.job_id,
                    row.tenant_id,
                    age,
                )
        await session.commit()

    return reaped


async def run_periodic_deck_reaper(
    session_factory: Any,
    redis: RedisClient | None,
    *,
    stale_threshold_seconds: int,
    interval_seconds: int,
) -> None:
    """Run :func:`reap_stuck_deck_jobs` every ``interval_seconds`` for the pod's life.

    A single bad tick (transient DB/Redis error) must never kill the loop — it is
    logged and the sweep continues on the next interval.
    """
    logger.info(
        "Stuck-deck-job reaper started (interval=%ds, stale_threshold=%ds)",
        interval_seconds,
        stale_threshold_seconds,
    )
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            reaped = await reap_stuck_deck_jobs(
                session_factory, redis, stale_threshold_seconds=stale_threshold_seconds
            )
            if reaped:
                logger.info("reaper: reaped %d stuck deck job(s) this tick", reaped)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("deck reaper tick failed; continuing")
