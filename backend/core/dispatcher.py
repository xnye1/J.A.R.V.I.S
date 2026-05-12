"""
core/dispatcher.py — Priority-queue HUD notification dispatcher.

All code that wants to push a message to the HUD calls dispatcher.emit().
The dispatcher serialises concurrent service emissions via an asyncio
PriorityQueue so high-priority alerts never get buried behind telemetry.

Priority levels (lower int = higher priority):
  CRITICAL  1 — system failures, deploy errors, security alerts
  HIGH      2 — JARVIS AI responses, proactive alerts, dopamine warnings
  NORMAL    3 — telemetry status updates, calendar sync
  LOW       4 — informational logs, soft notifications
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger("jarvis.dispatcher")


class Priority:
    CRITICAL = 1
    HIGH     = 2
    NORMAL   = 3
    LOW      = 4


@dataclass(order=True)
class _Notification:
    """Comparable wrapper so PriorityQueue can rank by priority int."""

    priority: int
    seq:      int                    = field(compare=True)   # FIFO within same priority
    payload:  dict                   = field(compare=False)

    # seq is auto-incremented by the dispatcher to guarantee stable FIFO order


BroadcastFn = Callable[[dict], Awaitable[None]]


class HUDDispatcher:
    """
    Async priority queue that fans out HUD payloads through a single
    registered broadcast function.

    Usage
    -----
    dispatcher.set_broadcast(manager.broadcast_hud)
    await dispatcher.emit({"type": "status", ...}, Priority.NORMAL)

    The dispatcher.run() coroutine must be launched as an asyncio task
    during application lifespan.
    """

    def __init__(self) -> None:
        self._queue:        asyncio.PriorityQueue[_Notification] = asyncio.PriorityQueue()
        self._broadcast_fn: BroadcastFn | None = None
        self._seq:          int = 0
        self._running:      bool = False

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_broadcast(self, fn: BroadcastFn) -> None:
        """Register the function that actually sends messages to HUD clients."""
        self._broadcast_fn = fn

    # ── Emit ──────────────────────────────────────────────────────────────────

    async def emit(
        self,
        payload: dict,
        priority: int = Priority.NORMAL,
    ) -> None:
        """
        Enqueue a HUD notification.

        Parameters
        ----------
        payload  : JSON-serialisable dict; must contain a ``type`` key.
        priority : Priority.CRITICAL … Priority.LOW (1-4).
        """
        self._seq += 1
        await self._queue.put(_Notification(priority=priority, seq=self._seq, payload=payload))

    def emit_nowait(self, payload: dict, priority: int = Priority.NORMAL) -> None:
        """Non-async convenience wrapper (from sync code)."""
        self._seq += 1
        self._queue.put_nowait(_Notification(priority=priority, seq=self._seq, payload=payload))

    # ── Runner ────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Consume the queue forever — run as an asyncio Task during lifespan.
        Delivers each notification through the registered broadcast function.
        """
        self._running = True
        log.info("[Dispatcher] Priority queue online.")
        while self._running:
            notif = await self._queue.get()
            if self._broadcast_fn is not None:
                try:
                    await self._broadcast_fn(notif.payload)
                except Exception as exc:
                    log.warning("[Dispatcher] Broadcast error: %s", exc)
            self._queue.task_done()

    def stop(self) -> None:
        self._running = False

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()


# Module-level singleton
dispatcher = HUDDispatcher()
