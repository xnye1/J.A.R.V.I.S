"""
client/focus_score.py — Focus Score Engine.

Analyzes live anger-gauge readings to produce a 0-100 learning efficiency score:

  score = 100 - EWMA(gauge, α=0.12)

Triggers:
  score > 70  for any reading  → Ghost Mode ON  (overlay dims to 28%)
  score ≤ 70  recovery         → Ghost Mode OFF (overlay returns to 88%)
  score ≤ 60  sustained 30 min → POST /alert  "Break Mode" recommendation

Hourly analysis:
  hourly_scores() buckets the raw gauge history into 1-hour windows and
  returns mean efficiency per bucket — used by future analytics surfaces.

This engine runs entirely in the asyncio WS thread; its on_ghost_mode
callback is a pyqtSignal.emit() call which is thread-safe (QueuedConnection).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Callable

log = logging.getLogger("jarvis.focus_score")

EWMA_ALPHA       = 0.12    # smoothing factor (higher → more reactive)
GHOST_THRESHOLD  = 70.0    # score above this → ghost mode
BREAK_THRESHOLD  = 60.0    # score at/below this → approaching burnout
BREAK_DURATION_S = 30 * 60 # 30 min sustained low score → break recommendation
HISTORY_CAPACITY = 240     # ~2 hours at 10-second anger_update intervals


class FocusScoreEngine:
    """
    Receives raw gauge float on every anger_update event.
    Maintains EWMA-smoothed score and triggers ghost/break events.

    Usage (asyncio thread):
        engine = FocusScoreEngine(
            on_ghost_mode=_ghost_bridge.toggled.emit,
            http_base="http://158.180.78.104:8000",
        )
        # In WS receive loop:
        engine.record_gauge(msg["gauge"])
    """

    def __init__(
        self,
        on_ghost_mode: Callable[[bool], None],
        http_base: str,
    ) -> None:
        self._ghost_cb   = on_ghost_mode
        self._http_base  = http_base.rstrip("/")

        self._history: deque[tuple[float, float]] = deque(maxlen=HISTORY_CAPACITY)
        self._ewma:         float = 0.0
        self._score:        float = 100.0

        self._ghost_active:    bool  = False
        self._low_since:       float | None = None   # ts when score first hit ≤60
        self._break_sent:      bool  = False

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def current_score(self) -> float:
        return round(self._score, 1)

    def record_gauge(self, gauge: float) -> None:
        """Process one anger_update reading. Called from the asyncio WS thread."""
        now = time.time()
        self._history.append((now, float(gauge)))

        # Update EWMA
        self._ewma  = (1.0 - EWMA_ALPHA) * self._ewma + EWMA_ALPHA * gauge
        self._score = max(0.0, min(100.0, 100.0 - self._ewma))

        self._evaluate_ghost()
        self._evaluate_break()

    def hourly_scores(self) -> list[dict]:
        """
        Return per-hour efficiency summary for the last 2 hours.
        [{hour_offset: 0, score: 78.3, samples: 36}, ...]
        hour_offset=0 is the current hour, 1 is the previous hour.
        """
        now = time.time()
        result = []
        for offset in range(2):
            t_end   = now - offset * 3600
            t_start = t_end - 3600
            gauges  = [g for ts, g in self._history if t_start <= ts < t_end]
            score   = round(100.0 - (sum(gauges) / len(gauges)), 1) if gauges else None
            result.append({"hour_offset": offset, "score": score, "samples": len(gauges)})
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _evaluate_ghost(self) -> None:
        should_ghost = self._score > GHOST_THRESHOLD
        if should_ghost != self._ghost_active:
            self._ghost_active = should_ghost
            self._ghost_cb(should_ghost)
            log.debug(
                "[FocusScore] Ghost mode → %s  (score=%.1f)",
                "ON" if should_ghost else "OFF",
                self._score,
            )

    def _evaluate_break(self) -> None:
        now = time.time()
        if self._score <= BREAK_THRESHOLD:
            if self._low_since is None:
                self._low_since = now
                log.debug("[FocusScore] Low score started (%.1f ≤ %.0f)", self._score, BREAK_THRESHOLD)
            elif not self._break_sent and (now - self._low_since) >= BREAK_DURATION_S:
                self._break_sent = True
                asyncio.create_task(self._send_break_alert())
        else:
            if self._low_since is not None:
                log.debug("[FocusScore] Score recovered (%.1f). Reset break timer.", self._score)
            self._low_since   = None
            self._break_sent  = False

    async def _send_break_alert(self) -> None:
        import aiohttp  # lazy
        scores_str = "  ".join(
            f"H-{s['hour_offset']}: {s['score']}"
            for s in self.hourly_scores()
            if s["score"] is not None
        )
        msg = (
            f"⚡ Break Mode 권고 — 30분간 학습 효율 {self._score:.0f}점 지속. "
            f"({scores_str})  15분 휴식 후 재집중하세요."
        )
        log.info("[FocusScore] %s", msg)
        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    f"{self._http_base}/alert",
                    json={"message": msg, "severity": "HIGH"},
                    timeout=aiohttp.ClientTimeout(total=6),
                )
        except Exception as exc:
            log.warning("[FocusScore] Break alert send failed: %s", exc)
