"""
local/laptop_agent.py — Playwright singleton for JARVIS laptop automation.

Run this ON YOUR LAPTOP (not on Oracle server):
  pip install playwright websockets
  playwright install chromium
  python backend/local/laptop_agent.py

What it does:
  1. Opens Chrome to the JARVIS HUD (https://jarvis-yejun.duckdns.org/hud)
  2. Connects to JARVIS WebSocket as a 'laptop' device
  3. Keeps the browser alive; reopens if closed
  4. Listens for server commands: open_browser / close_browser / reload_browser
  5. Singleton lock prevents duplicate browser instances
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("jarvis.laptop")

SERVER_URL  = os.getenv("JARVIS_URL", "https://jarvis-yejun.duckdns.org")
WS_URL      = SERVER_URL.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
HUD_URL     = SERVER_URL + "/hud"
RECONNECT_S = 5   # WebSocket reconnect delay


# ── Playwright browser singleton ──────────────────────────────────────────────

class BrowserSingleton:
    """Manages a single Chromium browser instance. Thread-safe via asyncio lock."""

    def __init__(self) -> None:
        self._lock     = asyncio.Lock()
        self._pw       = None   # Playwright instance
        self._browser  = None   # Browser instance
        self._page     = None   # Active page

    async def start(self) -> None:
        """Launch Chromium if not already running."""
        async with self._lock:
            if self._browser and self._browser.is_connected():
                log.info("Browser already open — skipping duplicate launch.")
                return
            try:
                from playwright.async_api import async_playwright
                self._pw      = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=False,
                    args=["--start-maximized", "--disable-infobars"],
                )
                ctx = await self._browser.new_context(no_viewport=True)
                self._page = await ctx.new_page()
                await self._page.goto(HUD_URL, wait_until="domcontentloaded")
                log.info("HUD opened: %s", HUD_URL)
            except Exception as exc:
                log.error("Browser launch failed: %s", exc)
                await self._cleanup()

    async def reload(self) -> None:
        async with self._lock:
            if self._page:
                try:
                    await self._page.reload(wait_until="domcontentloaded")
                    log.info("HUD reloaded.")
                except Exception as exc:
                    log.warning("Reload failed: %s", exc)

    async def close(self) -> None:
        async with self._lock:
            await self._cleanup()

    async def _cleanup(self) -> None:
        for obj in (self._browser, self._pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
        self._pw = self._browser = self._page = None

    def is_alive(self) -> bool:
        return bool(self._browser and self._browser.is_connected())


browser = BrowserSingleton()


# ── WebSocket client ──────────────────────────────────────────────────────────

async def ws_loop() -> None:
    """Connect to JARVIS WS, register as 'laptop', handle commands."""
    try:
        import websockets
    except ImportError:
        log.error("pip install websockets  ← required")
        sys.exit(1)

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({"type": "register", "device": "remote"}))
                log.info("Connected to JARVIS: %s", WS_URL)

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    mtype = msg.get("type", "")

                    if mtype == "open_browser":
                        log.info("Command: open_browser")
                        asyncio.create_task(browser.start())

                    elif mtype == "close_browser":
                        log.info("Command: close_browser")
                        asyncio.create_task(browser.close())

                    elif mtype == "reload_browser":
                        log.info("Command: reload_browser")
                        asyncio.create_task(browser.reload())

                    elif mtype == "connected":
                        log.info("JARVIS link established — opening HUD.")
                        asyncio.create_task(browser.start())

        except Exception as exc:
            log.warning("WS disconnected (%s) — retrying in %ds", exc, RECONNECT_S)
            await asyncio.sleep(RECONNECT_S)


# ── Watchdog: reopen browser if user closes it ────────────────────────────────

async def browser_watchdog() -> None:
    """Check every 30s if browser is still alive; reopen if closed."""
    await asyncio.sleep(15)   # initial grace period
    while True:
        if not browser.is_alive():
            log.info("Browser closed — reopening HUD.")
            await browser.start()
        await asyncio.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    log.info("JARVIS Laptop Agent starting — HUD: %s", HUD_URL)
    await asyncio.gather(
        ws_loop(),
        browser_watchdog(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Laptop agent stopped.")
