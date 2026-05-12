"""
core/fury_tracker.py — Phone session time → AngerEngine input.

Algorithm
---------
When partner's phone connects:
  • Session timer starts.

Every minute (background tick):
  • If a focus session is active AND phone has been online > BLOCK_INTERVAL_MIN
    → fire anger.record_dopamine_block()

Per chat message during focus session:
  • Every MSG_BLOCK_THRESHOLD messages → fire block

Phone idle > IDLE_DECAY_MIN without messages:
  • Decay one block off the dopamine counter (capped: never go below 0)

Phone disconnects:
  • Session ends, timers reset.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from core.anger_engine import anger


# ── Tunables ──────────────────────────────────────────────────────────────────
BLOCK_INTERVAL_MIN   = 5    # minutes of continuous phone use → 1 block
MSG_BLOCK_THRESHOLD  = 5    # messages sent during focus session → 1 block
IDLE_DECAY_MIN       = 15   # minutes idle → decay 1 block


@dataclass
class PhoneSession:
    started_at:      float = field(default_factory=time.monotonic)
    last_active_at:  float = field(default_factory=time.monotonic)
    message_count:   int   = 0
    blocks_this_ses: int   = 0
    last_block_min:  float = 0.0   # elapsed minutes when last block was fired

    @property
    def elapsed_min(self) -> float:
        return (time.monotonic() - self.started_at) / 60.0

    @property
    def idle_min(self) -> float:
        return (time.monotonic() - self.last_active_at) / 60.0


class FuryTracker:
    """
    Singleton — import the module-level `fury` instance.

    Call on_connect / on_message / on_disconnect from the WebSocket handler.
    Call tick() from a periodic async task (every 60 s).
    """

    def __init__(self) -> None:
        self._session: Optional[PhoneSession] = None

    # ── WebSocket lifecycle callbacks ─────────────────────────────────────────

    def on_connect(self) -> None:
        self._session = PhoneSession()

    def on_message(self, focus_active: bool = False) -> None:
        if self._session is None:
            return
        self._session.last_active_at = time.monotonic()
        self._session.message_count += 1

        if focus_active and self._session.message_count % MSG_BLOCK_THRESHOLD == 0:
            anger.record_dopamine_block()
            self._session.blocks_this_ses += 1

    def on_disconnect(self) -> None:
        self._session = None

    # ── Periodic tick (call every ~60 s) ─────────────────────────────────────

    def tick(self, focus_active: bool = False) -> dict:
        """
        Run scheduled checks. Returns a status dict for logging/HUD.
        Should be called from an async task every 60 seconds.
        """
        if self._session is None:
            return {"active": False}

        sess = self._session
        elapsed = sess.elapsed_min
        idle    = sess.idle_min

        # Continuous use block
        since_last = elapsed - sess.last_block_min
        if focus_active and since_last >= BLOCK_INTERVAL_MIN:
            anger.record_dopamine_block()
            sess.blocks_this_ses += 1
            sess.last_block_min = elapsed

        # Idle decay (only when not in focus — forgiveness period)
        if not focus_active and idle >= IDLE_DECAY_MIN and anger._dopamine_blocks > 0:
            anger._dopamine_blocks = max(0, anger._dopamine_blocks - 1)
            anger._recalculate()

        snap = anger.snapshot()
        return {
            "active":          True,
            "elapsed_min":     round(elapsed, 1),
            "idle_min":        round(idle, 1),
            "messages":        sess.message_count,
            "blocks_session":  sess.blocks_this_ses,
            "anger_gauge":     snap["gauge"],
            "anger_stage":     snap["stage"],
        }

    @property
    def connected(self) -> bool:
        return self._session is not None

    def status(self) -> dict:
        if self._session is None:
            return {"connected": False, "anger_gauge": anger.gauge, "anger_stage": anger.profile.name}
        return {
            "connected":       True,
            "elapsed_min":     round(self._session.elapsed_min, 1),
            "messages":        self._session.message_count,
            "blocks_session":  self._session.blocks_this_ses,
            "anger_gauge":     anger.gauge,
            "anger_stage":     anger.profile.name,
        }


# Module-level singleton
fury = FuryTracker()
