"""
services/productivity_service.py — Productivity hub skeleton.

Features (D-Day injection targets):
  - Task board (Today / In Progress / Done)
  - Goal tracker with streak counter
  - Schedule optimizer (dead-time detection)
  - Focus score aggregator (wraps DopamineGuard metrics)

All methods return mock data until Phase 16 AI injection.
"""

from __future__ import annotations

import random
from typing import Any

from core.anger_engine import anger
from services.base_service import BaseService

# ── Mock pools ────────────────────────────────────────────────────────────────

_TASKS = [
    "Finish JARVIS Phase 15", "Review linear algebra notes",
    "30-min run", "Read 20 pages", "Write daily journal",
    "Code review backend/api", "Email professor",
]
_GOAL_CATEGORIES = ["Health", "Learning", "Career", "Finance", "Personal"]


class ProductivityService(BaseService):
    """
    Productivity hub — tasks, goals, schedule, focus score.
    Phase 16: integrate with real calendar API + goal DB.
    """

    @property
    def name(self) -> str:
        return "productivity_service"

    @property
    def display_name(self) -> str:
        return "Productivity Hub"

    async def start(self) -> None:
        self._mark_online()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Mock API surface ──────────────────────────────────────────────────────

    def get_task_board(self) -> dict[str, Any]:
        """Today's task distribution."""
        total  = random.randint(4, 10)
        done   = random.randint(0, total)
        in_prog = min(random.randint(0, 3), total - done)
        return {
            "total":       total,
            "done":        done,
            "in_progress": in_prog,
            "todo":        total - done - in_prog,
            "completion_pct": round(done / total * 100),
            "next_task":   random.choice(_TASKS),
        }

    def get_goal_tracker(self) -> dict[str, Any]:
        """Active goal streaks and weekly progress."""
        return {
            "active_goals":  random.randint(2, 6),
            "streak_days":   random.randint(0, 30),
            "best_streak":   random.randint(10, 60),
            "weekly_hit_pct": random.randint(50, 100),
            "focus_category": random.choice(_GOAL_CATEGORIES),
        }

    def get_schedule_gaps(self) -> dict[str, Any]:
        """Detected dead-time slots for optimization."""
        return {
            "gaps_found":   random.randint(0, 4),
            "largest_gap_min": random.choice([15, 30, 45, 60, 90]),
            "suggestion": "Use 14:00–14:30 gap for quiz review, Sir.",
        }

    def get_focus_score(self) -> dict[str, Any]:
        """Composite focus score from all productivity signals."""
        score = random.randint(40, 98)
        return {
            "score":     score,
            "grade":     "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D",
            "vs_avg":    random.randint(-15, 20),
            "dopamine_alerts": random.randint(0, 3),
        }

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        board = self.get_task_board()
        focus = self.get_focus_score()
        goals = self.get_goal_tracker()

        # Feed anger engine — efficiency from focus score, failure from task completion
        efficiency = float(focus["score"])
        goal_fail  = round(1.0 - board["completion_pct"] / 100.0, 3)
        anger.update_efficiency(efficiency)
        anger.update_goal_fail_rate(goal_fail)

        return {
            "type": "productivity_update",
            "data": {
                "focus_score":     focus["score"],
                "focus_grade":     focus["grade"],
                "tasks_done":      board["done"],
                "tasks_total":     board["total"],
                "goal_streak":     goals["streak_days"],
                "dopamine_alerts": focus["dopamine_alerts"],
            },
        }
