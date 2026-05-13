"""
client/logger.py — SQLite-only logging handler.

Replaces all root logging handlers with a single SQLiteHandler so no text
log files are ever created.  Uses only stdlib (sqlite3, logging, threading)
— zero extra RAM from third-party packages.

Schema: logs(id, ts REAL, level TEXT, module TEXT, message TEXT)
Index on ts for time-range queries.

Usage:
    from client.logger import configure_logging
    configure_logging()            # ~/.jarvis/jarvis.db default
    configure_logging("my.db")    # custom path
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    level   TEXT    NOT NULL,
    module  TEXT    NOT NULL,
    message TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_ts    ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
"""

_NOISY_LIBS = ("websockets", "urllib3", "asyncio", "aiohttp", "aiohttp.access")


class SQLiteHandler(logging.Handler):
    """
    Thread-safe logging.Handler that writes records to a SQLite database.
    A per-call connection is used (write-ahead mode) so multiple threads
    can log concurrently without blocking.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__()
        self._db = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db) as conn:
            conn.executescript(_SCHEMA)
            # WAL mode: readers don't block writers
            conn.execute("PRAGMA journal_mode=WAL")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                with sqlite3.connect(self._db) as conn:
                    conn.execute(
                        "INSERT INTO logs (ts, level, module, message) VALUES (?,?,?,?)",
                        (record.created, record.levelname, record.name, msg),
                    )
        except Exception:
            self.handleError(record)

    # ── Query helpers (for future debug UI) ───────────────────────────────────

    def tail(self, n: int = 50) -> list[dict]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ts, level, module, message FROM logs ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def query(self, level: str | None = None, since_ts: float = 0.0, limit: int = 200) -> list[dict]:
        sql = "SELECT ts, level, module, message FROM logs WHERE ts > ?"
        params: list = [since_ts]
        if level:
            sql += " AND level = ?"
            params.append(level.upper())
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def configure_logging(
    db_path: str | Path = Path.home() / ".jarvis" / "jarvis.db",
    level: int = logging.DEBUG,
) -> SQLiteHandler:
    """
    Reconfigure the root logger to use SQLiteHandler exclusively.

    Clears all existing handlers (FileHandlers, StreamHandlers, etc.)
    so no text output is ever written. Returns the handler instance so
    callers can query logs programmatically.
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = SQLiteHandler(db_path)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)

    # Silence verbose third-party loggers to WARN — saves DB space
    for lib in _NOISY_LIBS:
        logging.getLogger(lib).setLevel(logging.WARNING)

    return handler
