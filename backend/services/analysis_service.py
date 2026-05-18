"""
services/analysis_service.py — Real Behavioural & Emotional Analysis.

Data sources:
  • anger_engine   → gauge (0-100) + stage (GENTLE…LOCKDOWN)
  • empathy_engine → condition score + label
  • state          → live session data (focus sessions, distractions, messages)
"""

from __future__ import annotations

from typing import Any

from core.anger_engine   import anger
from core.empathy_engine import empathy
from core.state          import state
from services.base_service import BaseService

# Map anger stage → mood label
_STAGE_TO_MOOD = {
    "GENTLE":   ("집중",        40),
    "SARCASTIC": ("약간 긴장",   55),
    "STERN":    ("스트레스",    70),
    "FURIOUS":  ("매우 긴장",   85),
    "LOCKDOWN": ("과부하",      95),
}

_NUDGES = {
    "rest":       "집중 세션이 길어졌습니다, Sir. 5분 휴식이 이후 생산성을 18% 회복시킵니다.",
    "focus":      "방해 요소가 많이 감지됐습니다. 다음 세션은 방해 차단 모드를 권장합니다.",
    "hydration":  "90분 이상 수분 보충 기록 없음 — 물 한 잔 추천드립니다, Sir.",
    "cool_down":  "분노 게이지가 높습니다. 짧은 산책으로 코티솔을 낮추는 것을 권장합니다.",
    "good":       "현재 컨디션이 양호합니다. 이 상태를 유지하십시오, Sir.",
}


class AnalysisService(BaseService):

    @property
    def name(self) -> str:
        return "analysis_service"

    @property
    def display_name(self) -> str:
        return "Behavioural Analysis"

    async def start(self) -> None:
        self._mark_online()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Real API ──────────────────────────────────────────────────────────────

    def get_mood_snapshot(self) -> dict[str, Any]:
        """Infer mood from anger gauge + stage."""
        snap    = anger.snapshot()
        gauge   = snap["gauge"]
        stage   = snap["stage"]

        mood_label, stress_pct = _STAGE_TO_MOOD.get(stage, ("알 수 없음", 50))

        # Empathy condition (high empathy score = rested & performing well)
        emp = empathy.snapshot()
        condition_label = emp.get("label", "Nominal")

        return {
            "mood":        mood_label,
            "mood_score":  max(0, 100 - int(gauge)),   # higher = better mood
            "stress":      f"{stress_pct}%",
            "anger_gauge": gauge,
            "anger_stage": stage,
            "condition":   condition_label,
        }

    def get_cognitive_load(self) -> dict[str, Any]:
        """
        Cognitive load from real session data:
          base 30 + anger contribution + distraction penalty
        """
        stats       = state.daily_study_stats()
        distractions = stats["distractions"]
        sessions    = stats["sessions"]
        gauge       = anger.snapshot()["gauge"]

        # Formula: anger = sustained stress, distractions = context-switch overhead
        load = int(30 + gauge * 0.35 + min(distractions * 6, 30))
        load = min(load, 100)

        level = "High" if load > 75 else "Medium" if load > 45 else "Low"

        nudge_key = "good"
        if gauge >= 70:
            nudge_key = "cool_down"
        elif distractions >= 4:
            nudge_key = "focus"
        elif sessions >= 3 and load > 60:
            nudge_key = "rest"
        elif load <= 35:
            nudge_key = "good"

        return {
            "load_pct":         load,
            "level":            level,
            "context_switches": distractions,
            "sessions":         sessions,
            "recommendation":   _NUDGES[nudge_key],
        }

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        mood = self.get_mood_snapshot()
        load = self.get_cognitive_load()
        return {
            "type": "analysis_update",
            "data": {
                "mood":         mood["mood"],
                "mood_score":   mood["mood_score"],
                "stress":       mood["stress"],
                "cog_load_pct": load["load_pct"],
                "cog_level":    load["level"],
                "nudge":        load["recommendation"][:60] + "…",
            },
        }
