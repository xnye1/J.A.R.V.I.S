"""
client/laptop_agent.py — JARVIS laptop-side tool executor.

Connects to the JARVIS server as device="laptop" and executes tool_calls
sent by the agent_loop (shell commands, browser operations, file I/O).

Run on the laptop (separate from the PyQt overlay):
    python -m client.laptop_agent

Environment:
    JARVIS_SERVER   host (default: jarvis-yejun.duckdns.org)
    JARVIS_PORT     port (default: 443 for wss)
    JARVIS_WS_SCHEME  ws | wss  (default: wss)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

import websockets  # type: ignore[import-untyped]

from services.browser_controller import browser, dispatch_tool, HEADLESS
import services.browser_controller as _bc

_bc.HEADLESS = False  # laptop = visible browser

log = logging.getLogger("jarvis.laptop_agent")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

_HOST   = os.getenv("JARVIS_SERVER",   "jarvis-yejun.duckdns.org")
_PORT   = os.getenv("JARVIS_PORT",     "443")
_SCHEME = os.getenv("JARVIS_WS_SCHEME","wss")
WS_URL  = f"{_SCHEME}://{_HOST}:{_PORT}/ws"


# ── Tool executors ────────────────────────────────────────────────────────────

async def _exec_shell(command: str) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return out.strip()[:4000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out (30 s)"
    except Exception as exc:
        return f"ERROR: {exc}"


async def _read_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8", errors="replace")[:4000]
    except Exception as exc:
        return f"ERROR: {exc}"


async def _write_file(path: str, content: str) -> str:
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {p}"
    except Exception as exc:
        return f"ERROR: {exc}"


async def _list_dir(path: str = "") -> str:
    try:
        p = Path(path).expanduser() if path else Path.home() / "Desktop"
        items = sorted(p.iterdir())
        return "\n".join(
            f"{'[DIR] ' if i.is_dir() else ''}{i.name}" for i in items[:80]
        )
    except Exception as exc:
        return f"ERROR: {exc}"


async def _get_active_window() -> str:
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
             "Sort-Object CPU -Desc | Select-Object -First 1).MainWindowTitle"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception as exc:
        return f"ERROR: {exc}"


async def _launch_app(app: str) -> str:
    _apps = {
        "chrome":      "chrome",
        "edge":        "msedge",
        "firefox":     "firefox",
        "notepad":     "notepad",
        "calculator":  "calc",
        "explorer":    "explorer",
        "vscode":      "code",
        "spotify":     "spotify",
        "discord":     "discord",
        "steam":       "steam",
        "obs":         "obs64",
        "powershell":  "powershell",
        "cmd":         "cmd",
    }
    exe = _apps.get(app.lower(), app)
    try:
        subprocess.Popen([exe], shell=True)
        return f"Launched: {exe}"
    except Exception as exc:
        return f"ERROR: {exc}"


_BROWSER_TOOLS = {"open_url", "browser_click", "browser_type",
                  "browser_get_text", "browser_screenshot"}

async def execute_tool(name: str, args: dict) -> str:
    if name in _BROWSER_TOOLS:
        return await dispatch_tool(name, args)
    if name == "shell_execute":
        return await _exec_shell(args.get("command", ""))
    if name == "read_file":
        return await _read_file(args.get("path", ""))
    if name == "write_file":
        return await _write_file(args.get("path", ""), args.get("content", ""))
    if name == "list_directory":
        return await _list_dir(args.get("path", ""))
    if name == "get_active_window":
        return await _get_active_window()
    if name == "launch_app":
        return await _launch_app(args.get("app", ""))
    return f"ERROR: unknown tool '{name}'"


# ── WebSocket loop ────────────────────────────────────────────────────────────

async def _run() -> None:
    backoff = 3.0
    while True:
        try:
            log.info("Connecting → %s", WS_URL)
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps({"type": "register", "device": "laptop"}))
                log.info("Laptop agent online — ready for tool_calls")
                backoff = 3.0

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") != "tool_call":
                        continue

                    tc_id   = msg.get("id",   "")
                    tc_name = msg.get("name", "")
                    tc_args = msg.get("args", {})
                    log.info("[Tool] %s(%s)", tc_name, list(tc_args.keys()))

                    result = await execute_tool(tc_name, tc_args)
                    log.info("[Tool] → %s", result[:120])

                    await ws.send(json.dumps({
                        "type":   "tool_result",
                        "id":     tc_id,
                        "result": result,
                    }))

        except Exception as exc:
            log.warning("WS error: %s — retry in %.0f s", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 30.0)


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Laptop agent stopped.")
