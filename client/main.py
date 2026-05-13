"""
client/main.py — J.A.R.V.I.S. client-side process entry point.

Starts in this order:
  1. Configure SQLite-only logging (no text files, ever)
  2. Launch PyQt6 overlay (blocks until window closed)

DormSyncManager is not started here by default — import and wire it from
whichever module needs to make network calls (e.g., a sensor thread).

Environment variables:
  JARVIS_SERVER   Server hostname/IP   (default: 158.180.78.104)
  JARVIS_PORT     Server port          (default: 8000)
  JARVIS_LOG_DB   Path to SQLite log DB (default: ~/.jarvis/jarvis.db)
"""

from __future__ import annotations

import os
from pathlib import Path

# ── 1. Logging must be configured before any other jarvis import ──────────────
from client.logger import configure_logging

_log_db = Path(os.getenv("JARVIS_LOG_DB", Path.home() / ".jarvis" / "jarvis.db"))
_handler = configure_logging(db_path=_log_db)

import logging
log = logging.getLogger("jarvis.client")

# ── 2. Resolve server coordinates ─────────────────────────────────────────────
_SERVER    = os.getenv("JARVIS_SERVER", "158.180.78.104")
_PORT      = int(os.getenv("JARVIS_PORT", "8000"))
_WS_URL    = f"ws://{_SERVER}:{_PORT}/ws"
_HTTP_BASE = f"http://{_SERVER}:{_PORT}"


def main() -> None:
    log.info("J.A.R.V.I.S. client starting — server=%s:%s", _SERVER, _PORT)
    log.info("Logs → %s", _log_db)

    from client.overlay import JarvisOverlay
    overlay = JarvisOverlay(ws_url=_WS_URL, http_base=_HTTP_BASE)
    overlay.run()
    # overlay.run() blocks until Qt window is closed


if __name__ == "__main__":
    main()
