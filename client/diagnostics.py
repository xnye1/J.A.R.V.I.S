"""
client/diagnostics.py — Self-Diagnostic System

ErrorFileHandler:
  logging.Handler that appends structured records to logs/error.log.
  Emits _DiagBridge.error_flash signal on ERROR+ so the HUD can react.

HeartbeatMonitor:
  Async coroutine that pings the backend /health endpoint every 5 s.
  Emits _DiagBridge.heartbeat_lost / heartbeat_ok on state transitions.

setup_diagnostics(log_dir, bridge):
  Installs ErrorFileHandler on the root "jarvis" logger.
  Returns the handler so callers can close it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("jarvis.diagnostics")


# ── Error file handler ────────────────────────────────────────────────────────

class ErrorFileHandler(logging.Handler):
    """
    Writes ERROR+ records to logs/error.log.
    Calls on_error(level, message) for HUD bridge integration.
    """

    def __init__(
        self,
        log_dir: Path,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(level=logging.ERROR)
        log_dir.mkdir(parents=True, exist_ok=True)
        self._path     = log_dir / "error.log"
        self._on_error = on_error
        self.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except Exception:
            self.handleError(record)

        if self._on_error is not None:
            try:
                self._on_error(record.levelname, record.getMessage())
            except Exception:
                pass


def setup_diagnostics(
    log_dir: Path,
    on_error: Callable[[str, str], None] | None = None,
) -> ErrorFileHandler:
    """
    Attach ErrorFileHandler to the root 'jarvis' logger.
    Returns the handler so callers can remove it when shutting down.
    """
    handler = ErrorFileHandler(log_dir, on_error=on_error)
    logging.getLogger("jarvis").addHandler(handler)
    log.info("[Diagnostics] Error log → %s", handler._path)
    return handler


# ── Heartbeat monitor ─────────────────────────────────────────────────────────

class HeartbeatMonitor:
    """
    Async heartbeat: GET /health every `interval_s` seconds.
    Calls on_lost() / on_restored() exactly once per transition.

    Usage (inside an asyncio event loop):
        monitor = HeartbeatMonitor(http_base, on_lost=..., on_restored=...)
        asyncio.create_task(monitor.run())
    """

    def __init__(
        self,
        http_base:   str,
        on_lost:     Callable[[], None],
        on_restored: Callable[[], None],
        interval_s:  float = 5.0,
    ) -> None:
        self._url        = http_base.rstrip("/") + "/health"
        self._on_lost    = on_lost
        self._on_restored = on_restored
        self._interval   = interval_s
        self._connected: bool | None = None   # None = first check pending

    async def run(self) -> None:
        """Run forever. Call asyncio.create_task(monitor.run())."""
        try:
            import aiohttp
        except ImportError:
            log.warning("[Heartbeat] aiohttp not available — monitor disabled")
            return

        log.info("[Heartbeat] Monitoring %s every %.0f s", self._url, self._interval)
        while True:
            ok = await self._ping(aiohttp)
            if ok and self._connected is not True:
                self._connected = True
                if self._connected is not None:   # skip first silent restore
                    try:
                        self._on_restored()
                    except Exception:
                        pass
            elif not ok and self._connected is not False:
                self._connected = False
                try:
                    self._on_lost()
                except Exception:
                    pass
            await asyncio.sleep(self._interval)

    async def _ping(self, aiohttp: Any) -> bool:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    self._url,
                    timeout=aiohttp.ClientTimeout(total=3.5),
                ) as resp:
                    return resp.status < 500
        except Exception as exc:
            log.debug("[Heartbeat] ping failed: %s", exc)
            return False
