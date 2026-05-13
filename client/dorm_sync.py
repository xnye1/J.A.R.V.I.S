"""
client/dorm_sync.py — Dormitory-aware network synchronisation manager.

When is_in_dorm=True  → all outbound requests are held in an in-memory queue.
When is_in_dorm=False → the queue drains immediately via POST /sync/bulk.

Design decisions:
  - aiohttp is lazy-imported inside _fire() — not loaded until a real request fires.
    This saves ~20 MB RAM if DormSync never leaves dorm mode during the session.
  - The queue is a plain list protected by asyncio.Lock; no external deps.
  - Re-queuing on flush failure preserves ordering (batch prepended, not appended).
  - is_in_dorm setter is intentionally synchronous so it can be called from any
    context (e.g., a tray menu callback). The flush coroutine is scheduled safely.

Thread-safety note:
  asyncio.Lock is not safe to acquire from non-async code.  The setter schedules
  _flush() on the running event loop instead of locking directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("jarvis.dorm_sync")


@dataclass
class _PendingRequest:
    method:    str
    path:      str
    payload:   dict[str, Any]
    queued_at: float = field(default_factory=time.time)


class DormSyncManager:
    """
    Network fence for dormitory use.

    Typical lifecycle:
        dorm = DormSyncManager(base_url="http://158.180.78.104:8000")
        dorm.is_in_dorm = True                                       # fence up

        await dorm.request("POST", "/calendar", {"events": [...]})  # queued silently
        await dorm.request("POST", "/reactor",  {"energy_saving": True})

        dorm.is_in_dorm = False                                      # fence drops → bulk flush
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._queue: list[_PendingRequest] = []
        self._in_dorm: bool = False
        self._lock = asyncio.Lock()
        self._session = None  # aiohttp.ClientSession — lazy

    # ── Dorm flag ──────────────────────────────────────────────────────────────

    @property
    def is_in_dorm(self) -> bool:
        return self._in_dorm

    @is_in_dorm.setter
    def is_in_dorm(self, value: bool) -> None:
        leaving = self._in_dorm and not value
        self._in_dorm = bool(value)
        if leaving:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._flush())
            except RuntimeError:
                # No running loop (e.g. called from sync context) — flush on next tick
                log.warning("[DormSync] No running event loop; flush deferred until loop starts.")

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict | None:
        """
        Send or queue a request depending on dorm state.

        Returns the server response dict when sent immediately.
        Returns None when queued (dorm mode active).
        """
        payload = payload or {}
        if self._in_dorm:
            async with self._lock:
                self._queue.append(_PendingRequest(method, path, payload))
            log.debug("[DormSync] Queued %s %s (depth=%d)", method, path, len(self._queue))
            return None
        return await self._fire(method, path, payload)

    async def close(self) -> None:
        """Release the HTTP session. Call on application shutdown."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _flush(self) -> None:
        """Drain the pending queue by posting a single /sync/bulk batch."""
        async with self._lock:
            if not self._queue:
                log.debug("[DormSync] Left dorm — queue empty, nothing to flush.")
                return
            batch = list(self._queue)
            self._queue.clear()

        log.info("[DormSync] Leaving dorm — flushing %d request(s) via /sync/bulk.", len(batch))
        bulk_payload = {
            "requests": [
                {
                    "method":    r.method,
                    "path":      r.path,
                    "payload":   r.payload,
                    "queued_at": r.queued_at,
                }
                for r in batch
            ]
        }
        try:
            result = await self._fire("POST", "/sync/dorm-bulk", bulk_payload)
            processed = (result or {}).get("processed", "?")
            failed    = (result or {}).get("failed", "?")
            log.info("[DormSync] /sync/bulk complete — processed=%s failed=%s", processed, failed)
        except Exception as exc:
            log.error("[DormSync] /sync/bulk failed (%s) — re-queuing %d item(s).", exc, len(batch))
            async with self._lock:
                self._queue = batch + self._queue  # preserve original order

    async def _fire(self, method: str, path: str, payload: dict) -> dict | None:
        """HTTP request executor. aiohttp is imported lazily here."""
        import aiohttp  # noqa: PLC0415 — intentional lazy import to save RAM at startup

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

        url = f"{self._base_url}{path}"
        async with self._session.request(method, url, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()
