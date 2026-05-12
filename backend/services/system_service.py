"""
services/system_service.py — System Assistant skeleton.

Features (D-Day injection targets):
  - Coding assistant (code review, bug triage, refactor suggestions)
  - Life automation hub (reminders, smart triggers, routines)
  - Shell command advisor
  - Dependency & security scanner

All methods return mock data until Phase 16 AI injection.
"""

from __future__ import annotations

import random
from typing import Any

from services.base_service import BaseService

# ── Mock pools ────────────────────────────────────────────────────────────────

_SUGGESTIONS = [
    "core/dispatcher.py: consider capping queue at 256 items to prevent memory growth.",
    "services/memory_service.py: ChromaDB adapter lacks retry logic on connection drop.",
    "main.py: _status_broadcaster interval could be configurable via env var.",
    "hud.html: DataCascade canvas should pause on tab hidden via Page Visibility API.",
]
_AUTOMATIONS = [
    "Morning brief scheduled: 09:00 daily.",
    "Auto-push reminder fires at 23:00 if uncommitted changes detected.",
    "Weekly KOSPI digest: every Monday 08:30.",
    "Study reminder triggers if no session logged by 20:00.",
]
_LANGUAGES = ["Python", "JavaScript", "TypeScript", "Bash", "YAML", "SQL"]


class SystemService(BaseService):
    """
    System assistant — coding help, automation, shell advice.
    Phase 16: integrate with tree-sitter AST + shell execution sandbox.
    """

    @property
    def name(self) -> str:
        return "system_service"

    @property
    def display_name(self) -> str:
        return "System Assistant"

    async def start(self) -> None:
        self._mark_online()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Mock API surface ──────────────────────────────────────────────────────

    def get_code_review(self, filepath: str = "unknown") -> dict[str, Any]:
        """Automated code review snapshot (mock)."""
        return {
            "file":        filepath,
            "issues":      random.randint(0, 4),
            "suggestion":  random.choice(_SUGGESTIONS),
            "complexity":  random.choice(["Low", "Medium", "High"]),
            "language":    random.choice(_LANGUAGES),
        }

    def get_automations(self) -> dict[str, Any]:
        """Active automations and next trigger (mock)."""
        return {
            "active_count": random.randint(2, 6),
            "runs_today":   random.randint(0, 8),
            "next_trigger": random.choice(_AUTOMATIONS),
            "errors":       random.randint(0, 1),
        }

    def get_dependency_scan(self) -> dict[str, Any]:
        """Dependency & security scan result (mock)."""
        return {
            "packages_checked": random.randint(12, 30),
            "outdated":         random.randint(0, 5),
            "vulnerabilities":  random.randint(0, 2),
            "last_scan":        "today",
        }

    def get_shell_advice(self, intent: str = "") -> dict[str, Any]:
        """Natural language → safe shell command (mock)."""
        return {
            "intent":  intent or "list large files",
            "command": "find . -type f -size +10M | sort -k5 -rh",
            "safe":    True,
            "risk":    "None",
        }

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        auto = self.get_automations()
        scan = self.get_dependency_scan()
        return {
            "type": "sysassist_update",
            "data": {
                "automations":    auto["active_count"],
                "runs_today":     auto["runs_today"],
                "errors":         auto["errors"],
                "vulnerabilities": scan["vulnerabilities"],
                "outdated_pkgs":  scan["outdated"],
            },
        }
