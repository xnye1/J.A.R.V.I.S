"""
core/fury_tracker.py — Phone session time → AngerEngine input.

Multiplier ladder (highest wins):
  ACADEMY_BLOCK_MULTIPLIER = 2   during scheduled class hours
  WEEKEND_BLOCK_MULTIPLIER = 3   Fri 19:40 → end of Sunday (home stay)
  default                  = 1   all other times

Idle decay is suppressed during any active session window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from core.anger_engine  import anger
from core.empathy_engine import empathy
from core.stealth import is_academy_hour, is_weekend_hyperfocus


# ── Tunables ──────────────────────────────────────────────────────────────────
BLOCK_INTERVAL_MIN       = 5   # minutes of continuous phone use → 1 block
MSG_BLOCK_THRESHOLD      = 5   # messages during active session → 1 block
IDLE_DECAY_MIN           = 15  # minutes idle (no active session) → decay 1 block
ACADEMY_BLOCK_MULTIPLIER = 2   # class hour weight — 수업 중 딴짓은 치명적
WEEKEND_BLOCK_MULTIPLIER = 3   # weekend window weight — 최고 민감도


def _block_amount(focus_active: bool) -> tuple[bool, float]:
    """
    Returns (in_session, amount) based on current time, focus state, and user condition.

    Base multiplier (academy/weekend) is scaled by the EmpathyEngine's anger_multiplier
    so a tired/sleep-deprived user receives a gentler gauge rise.
    """
    academy = is_academy_hour()
    weekend = is_weekend_hyperfocus()
    in_session = focus_active or academy or weekend

    if academy:
        base = ACADEMY_BLOCK_MULTIPLIER
    elif weekend:
        base = WEEKEND_BLOCK_MULTIPLIER
    else:
        base = 1

    # Apply empathy scaling — no DB call at runtime; uses cached profile
    scaled = base * empathy.anger_multiplier
    return in_session, max(0.1, scaled)   # floor at 0.1 — never 0


@dataclass
class PhoneSession:
    started_at:      float = field(default_factory=time.monotonic)
    last_active_at:  float = field(default_factory=time.monotonic)
    message_count:   int   = 0
    blocks_this_ses: int   = 0
    last_block_min:  float = 0.0

    @property
    def elapsed_min(self) -> float:
        return (time.monotonic() - self.started_at) / 60.0

    @property
    def idle_min(self) -> float:
        return (time.monotonic() - self.last_active_at) / 60.0


class FuryTracker:
    """
    Singleton — import the module-level `fury` instance.
    """

    def __init__(self) -> None:
        self._session: Optional[PhoneSession] = None

    # ── WebSocket lifecycle ───────────────────────────────────────────────────

    def on_connect(self) -> None:
        self._session = PhoneSession()

    def on_message(self, focus_active: bool = False) -> None:
        if self._session is None:
            return
        self._session.last_active_at = time.monotonic()
        self._session.message_count += 1

        in_session, amount = _block_amount(focus_active)
        if not in_session:
            return

        if self._session.message_count % MSG_BLOCK_THRESHOLD == 0:
            anger.record_dopamine_block(amount=amount)
            self._session.blocks_this_ses += amount

    def on_disconnect(self) -> None:
        self._session = None

    # ── Periodic tick (every ~60 s) ───────────────────────────────────────────

    def tick(self, focus_active: bool = False) -> dict:
        if self._session is None:
            return {"active": False}

        sess    = self._session
        elapsed = sess.elapsed_min
        idle    = sess.idle_min

        in_session, amount = _block_amount(focus_active)

        # Continuous-use block
        since_last = elapsed - sess.last_block_min
        if in_session and since_last >= BLOCK_INTERVAL_MIN:
            anger.record_dopamine_block(amount=amount)
            sess.blocks_this_ses += amount
            sess.last_block_min = elapsed

        # Idle decay — only when no session active
        if not in_session and idle >= IDLE_DECAY_MIN and anger._dopamine_blocks > 0:
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
            "academy_hour":    is_academy_hour(),
            "weekend_focus":   is_weekend_hyperfocus(),
        }

    @property
    def connected(self) -> bool:
        return self._session is not None

    def status(self) -> dict:
        academy = is_academy_hour()
        weekend = is_weekend_hyperfocus()
        base = {
            "anger_gauge":   anger.gauge,
            "anger_stage":   anger.profile.name,
            "academy_hour":  academy,
            "weekend_focus": weekend,
            "multiplier":    ACADEMY_BLOCK_MULTIPLIER if academy else (WEEKEND_BLOCK_MULTIPLIER if weekend else 1),
        }
        if self._session is None:
            return {"connected": False, **base}
        return {
            "connected":      True,
            "elapsed_min":    round(self._session.elapsed_min, 1),
            "messages":       self._session.message_count,
            "blocks_session": self._session.blocks_this_ses,
            **base,
        }


fury = FuryTracker()
