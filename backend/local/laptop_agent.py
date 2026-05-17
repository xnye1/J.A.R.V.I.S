"""
local/laptop_agent.py — JARVIS Laptop Agent (Playwright + Tool Executor)

Run ON YOUR LAPTOP:
  pip install playwright websockets
  playwright install chromium
  python backend/local/laptop_agent.py

Capabilities:
  • Auto-opens JARVIS HUD in Chrome on startup
  • Executes tool_call commands from the server:
      shell_execute, open_url, browser_click, browser_type,
      browser_get_text, browser_screenshot, read_file, write_file,
      list_directory
  • Sends tool_result back to the server
  • Browser singleton — no duplicate instances
  • Watchdog — reopens HUD if closed
  • Auto-reconnects on WebSocket drop
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("jarvis.laptop")

SERVER_URL   = os.getenv("JARVIS_URL", "https://jarvis-yejun.duckdns.org")
WS_URL       = SERVER_URL.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
HUD_URL      = SERVER_URL + "/hud"
RECONNECT_S  = 5


# ── Browser singleton ─────────────────────────────────────────────────────────

class BrowserSingleton:
    def __init__(self) -> None:
        self._lock    = asyncio.Lock()
        self._pw      = None
        self._browser = None
        self._page    = None     # HUD page
        self._task_page = None   # page used for task browsing (separate from HUD)

    async def start(self) -> None:
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return
            try:
                from playwright.async_api import async_playwright
                self._pw      = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=False,
                    args=["--start-maximized", "--disable-infobars"],
                )
                ctx            = await self._browser.new_context(no_viewport=True)
                self._page     = await ctx.new_page()
                self._task_page = await ctx.new_page()  # separate tab for tasks
                await self._page.goto(HUD_URL, wait_until="domcontentloaded")
                log.info("HUD opened: %s", HUD_URL)
            except Exception as exc:
                log.error("Browser launch failed: %s", exc)
                await self._cleanup()

    async def navigate(self, url: str) -> str:
        await self._ensure()
        try:
            await self._task_page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return f"Navigated to {url}"
        except Exception as exc:
            return f"Navigation error: {exc}"

    async def click(self, selector: str) -> str:
        await self._ensure()
        try:
            if selector.startswith("text="):
                await self._task_page.get_by_text(selector[5:]).first.click(timeout=8000)
            else:
                await self._task_page.click(selector, timeout=8000)
            return f"Clicked: {selector}"
        except Exception as exc:
            return f"Click error: {exc}"

    async def type_text(self, selector: str, text: str) -> str:
        await self._ensure()
        try:
            await self._task_page.fill(selector, text, timeout=8000)
            return f"Typed into {selector}"
        except Exception as exc:
            return f"Type error: {exc}"

    async def get_text(self) -> str:
        await self._ensure()
        try:
            text = await self._task_page.inner_text("body")
            return text[:3000]   # cap size
        except Exception as exc:
            return f"Get text error: {exc}"

    async def screenshot(self) -> str:
        await self._ensure()
        try:
            path = os.path.join(os.path.expanduser("~"), "jarvis_screenshot.png")
            await self._task_page.screenshot(path=path, full_page=False)
            title = await self._task_page.title()
            url   = self._task_page.url
            return f"Screenshot saved. Page: '{title}' at {url}"
        except Exception as exc:
            return f"Screenshot error: {exc}"

    async def reload_hud(self) -> None:
        async with self._lock:
            if self._page:
                try:
                    await self._page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass

    async def close(self) -> None:
        async with self._lock:
            await self._cleanup()

    async def _ensure(self) -> None:
        if not self.is_alive():
            await self.start()

    async def _cleanup(self) -> None:
        for obj in (self._browser, self._pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
        self._pw = self._browser = self._page = self._task_page = None

    def is_alive(self) -> bool:
        return bool(self._browser and self._browser.is_connected())


browser = BrowserSingleton()


# ── Tool executor ─────────────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call and return the result string."""
    try:
        if name == "shell_execute":
            return _shell_execute(args.get("command", ""))

        elif name == "open_url":
            return await browser.navigate(args.get("url", "about:blank"))

        elif name == "browser_click":
            return await browser.click(args.get("selector", ""))

        elif name == "browser_type":
            return await browser.type_text(
                args.get("selector", ""), args.get("text", "")
            )

        elif name == "browser_get_text":
            return await browser.get_text()

        elif name == "browser_screenshot":
            return await browser.screenshot()

        elif name == "read_file":
            path = os.path.expanduser(args.get("path", ""))
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read(8000)
            return content

        elif name == "write_file":
            path = os.path.expanduser(args.get("path", ""))
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(args.get("content", ""))
            return f"Written: {path}"

        elif name == "list_directory":
            path = os.path.expanduser(args.get("path", "~/Desktop"))
            entries = os.listdir(path)
            return "\n".join(entries[:80])

        else:
            return f"Unknown tool: {name}"

    except Exception as exc:
        return f"[tool error] {name}: {exc}"


def _shell_execute(command: str) -> str:
    if not command.strip():
        return "No command provided."
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0 and err:
            return f"[rc={result.returncode}] {err[:1000]}"
        return (out or "(no output)")[:2000]
    except subprocess.TimeoutExpired:
        return "[timeout] Command exceeded 30 seconds."
    except Exception as exc:
        return f"[error] {exc}"


# ── WebSocket client ──────────────────────────────────────────────────────────

async def ws_loop() -> None:
    try:
        import websockets
    except ImportError:
        log.error("pip install websockets")
        sys.exit(1)

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({"type": "register", "device": "laptop"}))
                log.info("Connected to JARVIS server")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    mtype = msg.get("type", "")

                    if mtype == "connected":
                        log.info("JARVIS link established — launching HUD.")
                        asyncio.create_task(browser.start())

                    elif mtype == "tool_call":
                        tc_id   = msg.get("id", "")
                        tc_name = msg.get("name", "")
                        tc_args = msg.get("args", {})
                        log.info("Tool call: %s %s", tc_name, tc_args)

                        result = await execute_tool(tc_name, tc_args)
                        log.info("  → %s", result[:80])

                        await ws.send(json.dumps({
                            "type":   "tool_result",
                            "id":     tc_id,
                            "result": result,
                        }))

                    elif mtype == "open_browser":
                        asyncio.create_task(browser.start())
                    elif mtype == "close_browser":
                        asyncio.create_task(browser.close())
                    elif mtype == "reload_browser":
                        asyncio.create_task(browser.reload_hud())

        except Exception as exc:
            log.warning("WS disconnected (%s) — retrying in %ds…", exc, RECONNECT_S)
            await asyncio.sleep(RECONNECT_S)


# ── Browser watchdog ──────────────────────────────────────────────────────────

async def browser_watchdog() -> None:
    await asyncio.sleep(15)
    while True:
        if not browser.is_alive():
            log.info("Browser closed — reopening HUD.")
            await browser.start()
        await asyncio.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    log.info("JARVIS Laptop Agent | HUD: %s", HUD_URL)
    await asyncio.gather(ws_loop(), browser_watchdog())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Agent stopped.")
