"""
services/study_service.py — Study Coach skeleton.

Features (D-Day injection targets):
  - Pomodoro study timer with subject tracking
  - Exam D-Day countdown & milestone alerts
  - Spaced-repetition quiz generator
  - Progress heatmap data provider

All methods return mock data until Phase 16 AI injection.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from core.dispatcher import Priority
from services.base_service import BaseService

# ── Mock data pools ───────────────────────────────────────────────────────────

_SUBJECTS = [
    "Python Algorithms", "Linear Algebra", "System Design",
    "English Vocabulary", "Discrete Math", "OS Concepts",
]
_STATUS = ["Excellent", "Good", "Review Needed", "Behind Schedule"]
_TIPS   = [
    "Recall beats re-reading — close the book and write what you remember.",
    "Interleave subjects: switch every 25 min for better retention.",
    "Sleep is non-negotiable for memory consolidation, Sir.",
    "Teach it to explain it — the Feynman technique works.",
]


class StudyService(BaseService):
    """
    Study coach — tracks sessions, exams, and quiz progress.
    Phase 16: integrate with real calendar + spaced-repetition engine.
    """

    @property
    def name(self) -> str:
        return "study_service"

    @property
    def display_name(self) -> str:
        return "Study Coach"

    async def start(self) -> None:
        self._mark_online()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Mock API surface ──────────────────────────────────────────────────────

    def get_session_status(self) -> dict[str, Any]:
        """Current study session snapshot."""
        return {
            "subject":         random.choice(_SUBJECTS),
            "elapsed_minutes": random.randint(5, 90),
            "progress_pct":    random.randint(35, 98),
            "status":          random.choice(_STATUS),
            "tip":             random.choice(_TIPS),
        }

    def get_dday(self, exam_name: str = "Final Exam") -> dict[str, Any]:
        """Days remaining to next exam."""
        days_left = random.randint(3, 60)
        exam_date = (date.today() + timedelta(days=days_left)).isoformat()
        return {
            "exam":      exam_name,
            "date":      exam_date,
            "days_left": days_left,
            "urgency":   "CRITICAL" if days_left < 7 else "HIGH" if days_left < 21 else "NORMAL",
        }

    def get_quiz(self, subject: str | None = None) -> dict[str, Any]:
        """Next spaced-repetition quiz card (mock)."""
        subj = subject or random.choice(_SUBJECTS)
        return {
            "subject":     subj,
            "question":    f"[MOCK] Explain the key concept of {subj} in two sentences.",
            "difficulty":  random.choice(["Easy", "Medium", "Hard"]),
            "due_count":   random.randint(0, 8),
            "retention_pct": random.randint(55, 95),
        }

    def get_weekly_heatmap(self) -> dict[str, Any]:
        """7-day study activity heatmap data."""
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return {
            "heatmap": {d: random.randint(0, 4) for d in days},
            "total_hours": round(random.uniform(5, 25), 1),
            "best_day": random.choice(days),
        }

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        sess = self.get_session_status()
        dd   = self.get_dday()
        return {
            "type": "study_update",
            "data": {
                "subject":     sess["subject"],
                "progress":    sess["progress_pct"],
                "status":      sess["status"],
                "session_min": sess["elapsed_minutes"],
                "dday":        f"D-{dd['days_left']}",
                "quiz_due":    self.get_quiz()["due_count"],
            },
        }
