"""
services/study_service.py — Real Study Coach.

Data sources:
  • state.daily_study_stats()   → live focus sessions / minutes / distractions
  • Homework DB table           → nearest upcoming deadline (D-Day)
  • StudyPlan DB table          → real weak-subject quiz targets
  • DopamineGuard active flag   → current session status
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from core.dispatcher import Priority
from core.state import state
from services.base_service import BaseService

_TIPS = [
    "복습보다 회상 — 책 덮고 기억나는 것부터 적으세요, Sir.",
    "과목을 25분마다 전환하면 장기 기억 효율이 올라갑니다.",
    "수면은 협상 불가능합니다 — 기억 고착화는 자는 동안 일어납니다, Sir.",
    "파인만 기법: 설명할 수 없으면 아직 모르는 겁니다.",
    "능동적 인출 연습이 재독보다 2배 이상 효과적입니다.",
]


class StudyService(BaseService):

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

    # ── Real API ──────────────────────────────────────────────────────────────

    def get_session_status(self) -> dict[str, Any]:
        """Live study stats from state singleton (updated by DopamineGuard)."""
        stats   = state.daily_study_stats()
        active  = state.dopamine_guard_active
        return {
            "active":            active,
            "sessions_today":    stats["sessions"],
            "study_minutes":     stats["minutes"],
            "distraction_hits":  stats["distractions"],
            "tip":               random.choice(_TIPS),
        }

    def get_dday(self) -> dict[str, Any]:
        """Nearest pending homework deadline from DB."""
        from db.database import SessionLocal, Homework
        today_str = date.today().isoformat()

        try:
            with SessionLocal() as db:
                hw = (
                    db.query(Homework)
                    .filter(Homework.status == "pending")
                    .filter(Homework.deadline.isnot(None))
                    .filter(Homework.deadline >= today_str)
                    .order_by(Homework.deadline)
                    .first()
                )
                if hw:
                    days_left = (date.fromisoformat(hw.deadline) - date.today()).days
                    return {
                        "exam":      f"{hw.subject}: {hw.title}",
                        "date":      hw.deadline,
                        "days_left": days_left,
                        "urgency":   "CRITICAL" if days_left <= 1
                                     else "HIGH"   if days_left <= 3
                                     else "NORMAL",
                    }
        except Exception:
            pass

        return {"exam": "없음", "date": None, "days_left": None, "urgency": "NORMAL"}

    def get_weak_subject(self) -> dict[str, Any]:
        """Top weak subject from StudyPlan DB for proactive nudge."""
        from db.database import SessionLocal, StudyPlan
        try:
            with SessionLocal() as db:
                plan = (
                    db.query(StudyPlan)
                    .filter(StudyPlan.status != "mastered")
                    .order_by(StudyPlan.weakness_level.desc())
                    .first()
                )
                if plan:
                    return {
                        "subject":         plan.subject,
                        "topic":           plan.topic,
                        "weakness_level":  plan.weakness_level,
                    }
        except Exception:
            pass
        return {}

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        sess = self.get_session_status()
        dd   = self.get_dday()

        dday_label = f"D-{dd['days_left']}" if dd["days_left"] is not None else "없음"

        return {
            "type": "study_update",
            "data": {
                "active":      sess["active"],
                "sessions":    sess["sessions_today"],
                "minutes":     sess["study_minutes"],
                "distractions": sess["distraction_hits"],
                "dday":        dday_label,
                "dday_exam":   dd["exam"],
                "dday_urgency": dd["urgency"],
            },
        }
