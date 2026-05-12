"""
core/dorm_tracker.py — Dormitory isolation + weekly return briefing.

Lifecycle
---------
  on_location_update(zone) → call every time GPS zone changes.
    • zone == "dorm"       → records entry, clears offline buffer
    • zone != "dorm" (was) → records exit, generates return briefing

Offline buffer
--------------
  While in dorm, buffer_event(payload) stores any event locally.
  On exit, buffered events are returned in the briefing for the catch-up.

Return briefing
---------------
  Queries DB for last 5 days: sessions, fury history, goals, study plans.
  Returns a structured dict that the WS dispatcher emits as
  "dorm_exit_briefing" to all HUD clients.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("jarvis.dorm")


class DormTracker:

    def __init__(self) -> None:
        self._in_dorm:        bool             = False
        self._entered_at:     datetime | None  = None
        self._offline_buffer: list[dict]       = []

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def in_dorm(self) -> bool:
        return self._in_dorm

    def on_location_update(self, zone: str) -> dict | None:
        """
        Call on every GPS zone change.
        Returns briefing payload when exiting dorm, else None.
        """
        if zone == "dorm" and not self._in_dorm:
            self._enter()
            return None
        if zone != "dorm" and self._in_dorm:
            return self._exit(zone)
        return None

    def buffer_event(self, event: dict) -> None:
        """Store an event that occurred while in dorm (offline buffer)."""
        if self._in_dorm:
            self._offline_buffer.append({
                **event,
                "_buffered_at": datetime.now(timezone.utc).isoformat(),
            })

    def status(self) -> dict:
        return {
            "in_dorm":        self._in_dorm,
            "entered_at":     self._entered_at.isoformat() if self._entered_at else None,
            "buffered_events": len(self._offline_buffer),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _enter(self) -> None:
        self._in_dorm    = True
        self._entered_at = datetime.now(timezone.utc)
        self._offline_buffer.clear()
        log.info("Dorm entry recorded — standby mode engaged.")
        self._log_to_db(entered_at=self._entered_at)

    def _exit(self, new_zone: str) -> dict:
        self._in_dorm = False
        now = datetime.now(timezone.utc)
        duration_hrs = (
            (now - self._entered_at).total_seconds() / 3600
            if self._entered_at else 0.0
        )
        log.info("Dorm exit → %s after %.1f h", new_zone, duration_hrs)

        briefing = self._generate_briefing(duration_hrs, new_zone)
        self._log_to_db(exited_at=now, duration_hrs=duration_hrs)
        self._offline_buffer.clear()
        return briefing

    def _generate_briefing(self, duration_hrs: float, exit_zone: str) -> dict:
        sessions, fury_history, goals, study_plans = self._query_db()

        total_msgs        = sum(s.messages_sent    for s in sessions)
        total_distractions = sum(s.distraction_hits for s in sessions)
        avg_efficiency    = (
            sum(s.efficiency_score for s in sessions) / len(sessions)
            if sessions else 100.0
        )
        _STAGE_ORDER = ["GENTLE", "SARCASTIC", "STERN", "FURIOUS", "LOCKDOWN"]
        fury_peak = max(
            (f.stage for f in fury_history),
            default="GENTLE",
            key=lambda s: _STAGE_ORDER.index(s) if s in _STAGE_ORDER else 0,
        )

        critical_weaknesses = [
            sp for sp in study_plans if sp.weakness_level >= 4
        ]

        return {
            "type":           "dorm_exit_briefing",
            "exit_zone":      exit_zone,
            "duration_hrs":   round(duration_hrs, 1),
            "duration_days":  round(duration_hrs / 24, 1),
            "days_reviewed":  len(sessions),
            "total_messages": total_msgs,
            "total_distractions": total_distractions,
            "avg_efficiency": round(avg_efficiency, 1),
            "fury_peak":      fury_peak,
            "active_goals":   len([g for g in goals if g.status == "active"]),
            "goals":          [{"title": g.title, "progress": round(g.progress * 100)} for g in goals],
            "critical_topics": [{"subject": sp.subject, "topic": sp.topic} for sp in critical_weaknesses],
            "buffered_events": len(self._offline_buffer),
        }

    def _query_db(self) -> tuple[list, list, list, list]:
        try:
            from db.database import DailySession, FuryHistory, Goal, StudyPlan
            from db.session import get_db
            with get_db() as db:
                sessions = (
                    db.query(DailySession)
                    .order_by(DailySession.date.desc())
                    .limit(5)
                    .all()
                )
                fury_history = (
                    db.query(FuryHistory)
                    .order_by(FuryHistory.recorded_at.desc())
                    .limit(120)
                    .all()
                )
                goals = (
                    db.query(Goal)
                    .filter(Goal.status == "active")
                    .order_by(Goal.priority)
                    .all()
                )
                study_plans = (
                    db.query(StudyPlan)
                    .filter(StudyPlan.status != "mastered")
                    .order_by(StudyPlan.weakness_level.desc())
                    .all()
                )
            return sessions, fury_history, goals, study_plans
        except Exception as exc:
            log.warning("DB query for briefing failed: %s", exc)
            return [], [], [], []

    def _log_to_db(self, entered_at: datetime | None = None,
                   exited_at: datetime | None = None,
                   duration_hrs: float | None = None) -> None:
        try:
            from db.database import DormLog
            from db.session import get_db
            with get_db() as db:
                if entered_at and exited_at is None:
                    db.add(DormLog(entered_at=entered_at))
                elif exited_at:
                    row = (
                        db.query(DormLog)
                        .filter(DormLog.exited_at.is_(None))
                        .order_by(DormLog.id.desc())
                        .first()
                    )
                    if row:
                        row.exited_at    = exited_at
                        row.duration_hrs = duration_hrs
                        row.briefing_sent = True
        except Exception as exc:
            log.warning("DormLog DB write failed: %s", exc)


# Module-level singleton
dorm_tracker = DormTracker()
