"""
services/browser_controller.py — Singleton Playwright browser controller.

Maintains ONE persistent Chrome/Chromium instance between calls so that
URL-open commands execute instantly without cold-start overhead.

Usage (server-side, headless=True):
    from services.browser_controller import browser
    title = await browser.open_url("https://example.com")

Usage (laptop-side, headless=False):
    import services.browser_controller as bc
    bc.HEADLESS = False
    title = await bc.browser.open_url("https://example.com")

Gracefully degrades: if Playwright is not installed, all methods return
an error string instead of raising so callers don't crash.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("jarvis.browser")

HEADLESS: bool = True  # flip to False when running on the laptop


class BrowserController:
    """Singleton persistent Playwright browser with instant URL navigation."""

    def __init__(self) -> None:
        self._pw       = None
        self._browser  = None
        self._page     = None
        self._lock     = asyncio.Lock()
        self._ready    = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def _ensure(self) -> bool:
        """(Re)launch browser if not running. Returns False if Playwright missing."""
        if self._ready and self._browser and self._browser.is_connected():
            return True
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            log.warning(
                "[Browser] Playwright not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
            return False
        try:
            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._page  = await self._browser.new_page()
            self._ready = True
            log.info("[Browser] Chromium launched (headless=%s)", HEADLESS)
            return True
        except Exception as exc:
            log.error("[Browser] Launch failed: %s", exc)
            self._ready = False
            return False

    async def close(self) -> None:
        """Gracefully close browser and Playwright runtime."""
        self._ready = False
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = self._page = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
        log.info("[Browser] Closed.")

    # ── Navigation ─────────────────────────────────────────────────────────────

    async def open_url(self, url: str, new_tab: bool = False) -> str:
        """
        Navigate the persistent browser to url.
        Returns the page title on success, or an error string on failure.
        """
        async with self._lock:
            if not await self._ensure():
                return "ERROR: Playwright not available"
            try:
                if new_tab:
                    self._page = await self._browser.new_page()
                assert self._page is not None
                await self._page.bring_to_front()
                await self._page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                title = await self._page.title()
                log.info("[Browser] Opened: %s → %s", url, title)
                return title
            except Exception as exc:
                log.error("[Browser] Navigation failed (%s): %s", url, exc)
                return f"ERROR: {exc}"

    # ── Interaction ────────────────────────────────────────────────────────────

    async def click(self, selector: str) -> str:
        """Click an element by CSS selector or 'text=...' locator."""
        async with self._lock:
            if not await self._ensure():
                return "ERROR: Playwright not available"
            try:
                assert self._page is not None
                await self._page.click(selector, timeout=8_000)
                return f"Clicked: {selector}"
            except Exception as exc:
                return f"ERROR: {exc}"

    async def type_text(self, selector: str, text: str) -> str:
        """Focus an input field and type text into it."""
        async with self._lock:
            if not await self._ensure():
                return "ERROR: Playwright not available"
            try:
                assert self._page is not None
                await self._page.fill(selector, text, timeout=8_000)
                return f"Typed into {selector}"
            except Exception as exc:
                return f"ERROR: {exc}"

    async def get_text(self) -> str:
        """Return the visible text content of the current page (capped at 2000 chars)."""
        async with self._lock:
            if not await self._ensure():
                return "ERROR: Playwright not available"
            try:
                assert self._page is not None
                text = await self._page.inner_text("body")
                return text[:2000]
            except Exception as exc:
                return f"ERROR: {exc}"

    async def screenshot_base64(self) -> str:
        """Return a base64-encoded PNG screenshot of the current page."""
        import base64  # noqa: PLC0415
        async with self._lock:
            if not await self._ensure():
                return "ERROR: Playwright not available"
            try:
                assert self._page is not None
                raw = await self._page.screenshot(full_page=False)
                return base64.b64encode(raw).decode()
            except Exception as exc:
                return f"ERROR: {exc}"

    # ── Status ─────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "ready":    self._ready and bool(self._browser and self._browser.is_connected()),
            "headless": HEADLESS,
            "page_url": "",  # async; not fetched here to keep status sync
        }


# Module-level singleton — import and use directly
browser = BrowserController()


# ── Convenience tool dispatcher ───────────────────────────────────────────────

async def dispatch_tool(name: str, args: dict) -> str:
    """
    Route an agent tool_call to the browser singleton.
    Used by laptop_agent when it receives a tool_call from the server.
    """
    if name == "open_url":
        return await browser.open_url(args.get("url", ""))
    if name == "browser_click":
        return await browser.click(args.get("selector", ""))
    if name == "browser_type":
        return await browser.type_text(args.get("selector", ""), args.get("text", ""))
    if name == "browser_get_text":
        return await browser.get_text()
    if name == "browser_screenshot":
        b64 = await browser.screenshot_base64()
        return f"[screenshot: {len(b64)} bytes base64]" if not b64.startswith("ERROR") else b64
    return f"ERROR: unknown browser tool '{name}'"
