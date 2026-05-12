"""
services/analysis_service.py — Behavioural & Emotional Analysis skeleton.

Features (D-Day injection targets):
  - Mood / stress level detector (keyboard rhythm, session patterns)
  - Behaviour pattern analyser (routine consistency, anomaly detection)
  - Cognitive load estimator (context-switch frequency)
  - Wellbeing coach nudges

All methods return mock data until Phase 16 AI injection.
Note: real implementation requires explicit user consent for any biometric proxy.
"""

from __future__ import annotations

import random
from typing import Any

from services.base_service import BaseService

# ── Mock pools ────────────────────────────────────────────────────────────────

_MOODS       = ["Focused", "Neutral", "Fatigued", "Motivated", "Restless"]
_STRESS      = ["Low", "Moderate", "Elevated", "High"]
_PATTERNS    = [
    "Peak cognitive window detected: 10:00–12:30.",
    "Context switches up 22% vs last week — consider deep-work blocks.",
    "Evening routine consistency: 80% — on track.",
    "Late-night sessions (>23:00) correlated with next-day low focus score.",
]
_NUDGES      = [
    "You've been heads-down for 73 min, Sir. A 5-minute walk would restore 18% focus.",
    "Hydration reminder — no break logged in the past 90 minutes.",
    "Posture check: consider standing desk switch for next session.",
    "Cognitive load appears elevated. Switching to a simpler task may help.",
]


class AnalysisService(BaseService):
    """
    Behavioural and emotional analysis hub.
    Phase 16: wire to keyboard/mouse heuristics and session telemetry.
    """

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

    # ── Mock API surface ──────────────────────────────────────────────────────

    def get_mood_snapshot(self) -> dict[str, Any]:
        """Current inferred mood and stress level (mock)."""
        score = random.randint(30, 95)
        return {
            "mood":        random.choice(_MOODS),
            "mood_score":  score,
            "stress":      random.choice(_STRESS),
            "confidence":  random.randint(55, 90),
        }

    def get_behaviour_pattern(self) -> dict[str, Any]:
        """Weekly behavioural pattern analysis (mock)."""
        return {
            "pattern":          random.choice(_PATTERNS),
            "consistency_pct":  random.randint(50, 95),
            "anomalies_today":  random.randint(0, 2),
            "peak_hour":        random.choice(["09:00", "10:30", "14:00", "16:00"]),
        }

    def get_cognitive_load(self) -> dict[str, Any]:
        """Estimated cognitive load from session metrics (mock)."""
        load = random.randint(20, 95)
        return {
            "load_pct":          load,
            "level":             "High" if load > 80 else "Medium" if load > 50 else "Low",
            "context_switches":  random.randint(0, 12),
            "recommendation":    random.choice(_NUDGES),
        }

    def get_wellbeing_nudge(self) -> dict[str, Any]:
        """Proactive wellbeing suggestion (mock)."""
        return {
            "nudge":      random.choice(_NUDGES),
            "priority":   random.choice(["Suggestion", "Reminder", "Alert"]),
            "category":   random.choice(["Rest", "Hydration", "Movement", "Focus"]),
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
                "nudge":        load["recommendation"][:55] + "…",
            },
        }
