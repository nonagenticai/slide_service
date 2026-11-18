"""Two unbounded awaits could wedge a render pod permanently.

1. ``page.evaluate("document.fonts.ready")`` takes no timeout of its own and is
   NOT covered by ``page_timeout_ms`` (that argument belongs to ``set_content``).
   A promise that never settles held the leased pool slot forever.
2. ``BrowserPool.lease()``'s ``await idle.get()`` had no acquire timeout, so once
   every slot was wedged by (1) the pod deadlocked: every subsequent render
   waited forever on a queue nothing would ever refill.

Both tests wrap the call under test in ``asyncio.wait_for`` so the *test* fails
fast instead of hanging when the guard is missing.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.browser_pool import BrowserPool
from src.services.html_slide_renderer import render_slide_png

_BRAND = {"primary_color": "#123456", "font_family": "Inter"}


# ---------------------------------------------------------------------------
# 1. document.fonts.ready must not hang the render
# ---------------------------------------------------------------------------


class _HangingPage:
    def __init__(self) -> None:
        self.screenshot_taken = False
        self.closed = False

    async def set_content(self, _html: str, **_kw: Any) -> None:
        return None

    async def evaluate(self, expr: str) -> bool:
        if expr == "document.fonts.ready":
            await asyncio.Event().wait()  # fonts never settle
        return True

    async def screenshot(self, **_kw: Any) -> bytes:
        self.screenshot_taken = True
        return b"\x89PNG\r\n\x1a\n"

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _HangingPage) -> None:
        self._page = page

    async def new_page(self, **_kw: Any) -> _HangingPage:
        return self._page


class _FakePool:
    def __init__(self, page: _HangingPage) -> None:
        self._page = page

    @asynccontextmanager
    async def lease(self):  # type: ignore[no-untyped-def]
        yield _FakeBrowser(self._page)


@pytest.mark.asyncio
async def test_fonts_ready_that_never_settles_does_not_wedge_the_slot() -> None:
    """The render must bound the evaluate, shoot the slide, and release the page."""
    page = _HangingPage()
    settings = SimpleNamespace(render=SimpleNamespace(page_timeout_ms=200, device_scale_factor=1.0))

    with (
        patch("src.config.get_settings", return_value=settings),
        patch(
            "src.services.html_slide_renderer.get_browser_pool",
            new=AsyncMock(return_value=_FakePool(page)),
        ),
    ):
        png = await asyncio.wait_for(
            render_slide_png(layout_type="title", content={"title": "Hi"}, brand=_BRAND),
            timeout=5.0,
        )

    assert png.startswith(b"\x89PNG")
    assert page.screenshot_taken is True
    assert page.closed is True


# ---------------------------------------------------------------------------
# 2. A fully-leased pool must time out acquiring, not deadlock
# ---------------------------------------------------------------------------


def _fake_playwright() -> MagicMock:
    driver = MagicMock()
    driver.start = AsyncMock(return_value=MagicMock())
    return MagicMock(return_value=driver)


@pytest.mark.asyncio
async def test_lease_times_out_instead_of_deadlocking() -> None:
    """With every slot held, a further lease raises rather than waiting forever."""
    pool = BrowserPool(size=1, max_renders=10, acquire_timeout=0.2)
    browser = MagicMock()
    browser.is_connected = MagicMock(return_value=True)
    browser.close = AsyncMock()

    with (
        patch("playwright.async_api.async_playwright", _fake_playwright()),
        patch.object(BrowserPool, "_launch", AsyncMock(return_value=browser)),
    ):
        async with pool.lease():
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(pool.lease().__aenter__(), timeout=5.0)

        # The slot is returned on release, so the pool recovers.
        async with pool.lease() as b:
            assert b is browser


@pytest.mark.asyncio
async def test_lease_does_not_time_out_when_a_slot_is_free() -> None:
    """The timeout must not fire on the normal path."""
    pool = BrowserPool(size=1, max_renders=10, acquire_timeout=0.2)
    browser = MagicMock()
    browser.is_connected = MagicMock(return_value=True)
    browser.close = AsyncMock()

    with (
        patch("playwright.async_api.async_playwright", _fake_playwright()),
        patch.object(BrowserPool, "_launch", AsyncMock(return_value=browser)),
    ):
        for _ in range(3):
            async with pool.lease() as b:
                assert b is browser


@pytest.mark.asyncio
async def test_text_measurement_that_never_settles_releases_the_page() -> None:
    class HangingLayoutPage(_HangingPage):
        async def evaluate(self, expr: str) -> bool:
            if expr != "document.fonts.ready":
                await asyncio.Event().wait()
            return True

    page = HangingLayoutPage()
    settings = SimpleNamespace(render=SimpleNamespace(page_timeout_ms=200, device_scale_factor=1.0))
    with (
        patch("src.config.get_settings", return_value=settings),
        patch(
            "src.services.html_slide_renderer.get_browser_pool",
            new=AsyncMock(return_value=_FakePool(page)),
        ),
        pytest.raises(TimeoutError),
    ):
        await asyncio.wait_for(
            render_slide_png(layout_type="title", content={"title": "Hi"}, brand=_BRAND),
            timeout=5.0,
        )
    assert page.closed is True
    assert page.screenshot_taken is False
