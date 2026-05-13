"""
core/empathy_engine.py — Adaptive Fury Sensitivity (Empathy Engine).

Reads DailySession records to assess the user's current condition and adjusts
how quickly the Fury Gauge rises.  The core insight:

  When you're rested and performing well, JARVIS holds a higher standard.
  When you're tired or burnt out, JARVIS eases pressure — piling on helps no one.

ConditionScore formula (0–100):
  base = 100
  − sleep_deprived_days_last7 × 12         (max −60 if 5+ deprived nights)
  − clamp(|avg_study_h − OPTIMAL_STUDY_H|  × 6, 0, 24)  (under/over-studying)
  + efficiency_streak × 4                  (consecutive days ≥ 75% score)
  + goal_success_rate × 10                 (0–10 bonus)
  clamped to [5, 100]

Anger Multiplier ladder:
  condition ≥ 80  →  1.8   (peak form — highest standard, fastest gauge)
  condition 60–80 →  1.0   (nominal)
  condition 40–60 →  0.65  (below par — light touch)
  condition < 40  →  0.35  (empathy mode — near-standby sensitivity)

Applied in FuryTracker._block_amount() as a factor on the block amount.
Refreshes every 60 minutes (or on explicit call).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("jarvis.empathy")

OPTIMAL_STUDY_H   = 4.0    # sweet-spot daily study hours
REFRESH_INTERVAL  = 3600   # seconds between automatic DB re-reads


@dataclass(frozen=True)
class ConditionProfile:
    score:          float    # 0–100
    multiplier:     float    # anger sensitivity factor
    label:          str      # human-readable state
    sleep_deprived_days: int
    avg_study_hours:     float
    efficiency_streak:   int
    message:        str      # JARVIS can embed this in responses


_MULTIPLIER_LADDER = [
    (80.0, 1.80, "Peak",      "Operating at peak — I'll be holding you to the highest standard today, Sir."),
    (60.0, 1.00, "Nominal",   "Baseline condition. Standard expectations apply."),
    (40.0, 0.65, "Below Par", "Performance metrics suggest fatigue. I'll ease the pressure — rest is productive too."),
    ( 0.0, 0.35, "Empathy",   "Signs of significant strain detected. Entering empathy mode — recovery takes priority."),
]


class EmpathyEngine:
    """
    Singleton.  Reads DB and caches a ConditionProfile for up to REFRESH_INTERVAL s.
    Import the module-level `empathy` singleton.
    """

    _instance: Optional["EmpathyEngine"] = None

    def __new__(cls) -> "EmpathyEngine":
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._profile: Optional[ConditionProfile] = None
            obj._last_refresh: float = 0.0
            cls._instance = obj
        return cls._instance

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def profile(self) -> ConditionProfile:
        """Cached or freshly computed ConditionProfile."""
        if self._profile is None or (time.time() - self._last_refresh) > REFRESH_INTERVAL:
            self._refresh()
        return self._profile  # type: ignore[return-value]

    @property
    def anger_multiplier(self) -> float:
        return self.profile.multiplier

    def snapshot(self) -> dict:
        p = self.profile
        return {
            "condition_score":    round(p.score, 1),
            "anger_multiplier":   p.multiplier,
            "label":              p.label,
            "sleep_deprived_days": p.sleep_deprived_days,
            "avg_study_hours":    round(p.avg_study_hours, 2),
            "efficiency_streak":  p.efficiency_streak,
            "message":            p.message,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Query DB and rebuild ConditionProfile. Falls back to neutral on DB error."""
        try:
            profile = self._compute_from_db()
        except Exception as exc:
            log.warning("[Empathy] DB query failed (%s) — using neutral profile.", exc)
            profile = _neutral_profile()
        self._profile       = profile
        self._last_refresh  = time.time()
        log.info(
            "[Empathy] Refreshed — score=%.1f  multiplier=%.2f  label=%s",
            profile.score, profile.multiplier, profile.label,
        )

    def _compute_from_db(self) -> ConditionProfile:
        from db.database import DailySession
        from db.session  import get_db

        with get_db() as db:
            sessions = (
                db.query(DailySession)
                .order_by(DailySession.date.desc())
                .limit(7)
                .all()
            )

        if not sessions:
            return _neutral_profile()

        # ── Inputs ────────────────────────────────────────────────────────────
        sleep_dep_days = sum(1 for s in sessions if s.sleep_deprived)
        avg_study_h    = sum(s.study_hours for s in sessions) / len(sessions)

        # Efficiency streak: consecutive days (most recent first) at ≥75 score
        streak = 0
        for s in sessions:
            if (s.efficiency_score or 0) >= 75:
                streak += 1
            else:
                break

        # Goal success rate from latest session
        latest_fail = sessions[0].goal_fail_rate if sessions else 0.5
        goal_success = 1.0 - latest_fail

        # ── Score formula ─────────────────────────────────────────────────────
        score  = 100.0
        score -= sleep_dep_days * 12.0
        score -= min(abs(avg_study_h - OPTIMAL_STUDY_H) * 6.0, 24.0)
        score += streak * 4.0
        score += goal_success * 10.0
        score  = max(5.0, min(100.0, score))

        # ── Multiplier from ladder ────────────────────────────────────────────
        for threshold, mult, label, msg in _MULTIPLIER_LADDER:
            if score >= threshold:
                return ConditionProfile(
                    score=score, multiplier=mult, label=label,
                    sleep_deprived_days=sleep_dep_days,
                    avg_study_hours=avg_study_h,
                    efficiency_streak=streak,
                    message=msg,
                )
        return _neutral_profile()   # unreachable but safe


def _neutral_profile() -> ConditionProfile:
    return ConditionProfile(
        score=60.0, multiplier=1.0, label="Nominal",
        sleep_deprived_days=0, avg_study_hours=OPTIMAL_STUDY_H,
        efficiency_streak=0,
        message="Insufficient data — applying standard sensitivity.",
    )


# Module-level singleton
empathy = EmpathyEngine()
