"""Pooled, long-lived Chromium browsers for slide PNG rendering.

Copied — by design — from ``html_render_service``'s browser pool. slide_service
renders its slide previews by rasterising HTML/CSS with Playwright/Chromium
(``html_slide_renderer``), so it needs the same bounded pool of long-lived
Chromium processes: each render leases a browser, opens a fresh page, screenshots
a fixed 1920x1080 viewport, and returns it. This removes the per-call launch cost
(~0.5-1s + RAM churn) while the bounded pool size keeps the concurrency ceiling.

A long-lived Chromium ratchets memory over the pod's lifetime, so each browser is
**relaunched after N leases** (``browser_max_renders``) to bound the ratchet.
Combined with the fixed-viewport (``full_page=False``) screenshot — which never
allocates a tall scroll-height bitmap — peak per-render memory stays bounded.

slide_service is independently usable: this is a *copy* of the technique, not a
runtime dependency on html_render_service. The pool is a module global; the ASGI
lifespan starts/stops it, and a direct render call lazily starts it for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class PoolStatus(NamedTuple):
    """A non-mutating snapshot of the pool, for /health.

    ``capacity`` is the number of slots that exist — the slide-render concurrency
    this pod can actually deliver. It is 0 whenever the pool never started, and
    that state is *reachable in production*: ``_start_browser_pool`` in
    ``src/asgi.py`` is deliberately fail-soft, so a pod whose Chromium driver
    failed to launch comes up, serves every route, answers ``/health`` with
    ``healthy`` — and 500s every single deck render for the life of the pod.

    ``live`` counts slots currently holding a *connected* browser. It is
    deliberately NOT part of the healthy/unhealthy verdict: slots are launched
    lazily on first lease and relaunched when found stale, so ``live < capacity``
    is the normal steady state of an idle pod, not a fault.
    """

    started: bool
    capacity: int
    idle: int
    live: int


_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

#: Seconds a render may wait for a free pool slot before giving up.
#:
#: ``await idle.get()`` used to be unbounded, which turned any slot that got
#: stuck mid-render (see the ``document.fonts.ready`` guard in
#: ``html_slide_renderer``) into a permanent, pod-wide deadlock: every later
#: render queued behind it forever, the request never returned, and the deck job
#: sat at ``processing`` until the reaper failed it — with nothing in the logs
#: naming the pool. A bounded wait converts that into a single failed job with an
#: explicit reason while healthy slots keep serving.
#:
#: 300s is deliberately generous: a slide render is seconds, so this only fires
#: when slots are genuinely wedged rather than merely busy.
_DEFAULT_ACQUIRE_TIMEOUT_S = 300.0


class _Slot:
    """A reusable pool slot holding one browser and its lease count."""

    __slots__ = ("browser", "renders")

    def __init__(self) -> None:
        self.browser: Any = None
        self.renders: int = 0


class BrowserPool:
    """A fixed-size pool of long-lived Chromium browsers."""

    def __init__(
        self,
        *,
        size: int,
        max_renders: int,
        acquire_timeout: float = _DEFAULT_ACQUIRE_TIMEOUT_S,
    ) -> None:
        self._size = max(1, size)
        self._max_renders = max(1, max_renders)
        self._acquire_timeout = max(0.001, float(acquire_timeout))
        self._pw: Any = None
        self._idle: asyncio.Queue[_Slot] | None = None
        self._slots: list[_Slot] = []
        self._start_lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._idle is not None

    def status(self) -> PoolStatus:
        """Snapshot the pool for /health. Never starts it and never blocks."""
        live = 0
        for slot in self._slots:
            browser = slot.browser
            if browser is None:
                continue
            try:
                if browser.is_connected():
                    live += 1
            except Exception:  # a probe must never raise into /health
                logger.debug("Browser slot connectivity probe failed", exc_info=True)
        return PoolStatus(
            started=self._idle is not None,
            capacity=len(self._slots),
            idle=self._idle.qsize() if self._idle is not None else 0,
            live=live,
        )

    async def start(self) -> None:
        """Start the Playwright driver and seed the (lazily-launched) slots."""
        async with self._start_lock:
            if self._idle is not None:
                return
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            queue: asyncio.Queue[_Slot] = asyncio.Queue()
            for _ in range(self._size):
                slot = _Slot()
                self._slots.append(slot)
                queue.put_nowait(slot)
            self._idle = queue
            logger.info(
                "Browser pool started: size=%d, relaunch every %d leases",
                self._size,
                self._max_renders,
            )

    async def _launch(self) -> Any:
        return await self._pw.chromium.launch(args=_LAUNCH_ARGS)

    @asynccontextmanager
    async def lease(self) -> AsyncGenerator[Any]:
        """Lease a connected browser; blocks when all ``size`` are in use.

        (Re)launches the slot's browser if it has none, is disconnected (crashed),
        or has hit ``max_renders`` — bounding the long-lived-Chromium memory
        ratchet. Acquiring is the concurrency gate (queue depth == pool size).
        """
        if self._idle is None:
            await self.start()
        idle = self._idle
        if idle is None:  # pragma: no cover - start() always sets it
            raise RuntimeError("Browser pool failed to start")
        try:
            slot = await asyncio.wait_for(idle.get(), timeout=self._acquire_timeout)
        except TimeoutError:
            # Cancelling ``Queue.get`` never consumes an item, so no slot is lost.
            logger.error(
                "Browser pool exhausted: no free slot after %.0fs (size=%d) — "
                "failing this render instead of blocking forever",
                self._acquire_timeout,
                self._size,
            )
            raise TimeoutError(
                f"No browser pool slot became free within {self._acquire_timeout:.0f}s "
                f"(pool size {self._size}); slots are wedged"
            ) from None
        try:
            stale = (
                slot.browser is None
                or not slot.browser.is_connected()
                or slot.renders >= self._max_renders
            )
            if stale:
                if slot.browser is not None:
                    with contextlib.suppress(Exception):
                        await slot.browser.close()
                slot.browser = await self._launch()
                slot.renders = 0
            slot.renders += 1
            yield slot.browser
        finally:
            idle.put_nowait(slot)

    async def stop(self) -> None:
        """Close all browsers and stop the Playwright driver."""
        if self._idle is None:
            return
        for slot in self._slots:
            if slot.browser is not None:
                with contextlib.suppress(Exception):
                    await slot.browser.close()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
        self._idle = None
        self._slots = []
        self._pw = None
        logger.info("Browser pool stopped")


_pool: BrowserPool | None = None


def _build_pool() -> BrowserPool:
    from src.config import get_settings

    cfg = get_settings().render
    return BrowserPool(size=cfg.render_max_concurrency, max_renders=cfg.browser_max_renders)


async def get_browser_pool() -> BrowserPool:
    """Return the started module-global pool, creating + starting it on first use."""
    global _pool
    if _pool is None:
        _pool = _build_pool()
    if not _pool.started:
        await _pool.start()
    return _pool


def pool_status() -> PoolStatus:
    """Snapshot the module-global pool WITHOUT creating or starting it.

    ``get_browser_pool`` is start-on-demand, so a health check must not go
    through it: probing would *create* the very thing it is meant to report on
    and the probe would always pass.
    """
    pool = _pool
    if pool is None:
        return PoolStatus(started=False, capacity=0, idle=0, live=0)
    return pool.status()


async def init_browser_pool() -> None:
    """Eagerly start the pool (ASGI startup) so the first request isn't slow."""
    await get_browser_pool()


async def close_browser_pool() -> None:
    """Stop and drop the module-global pool (ASGI shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.stop()
        _pool = None
