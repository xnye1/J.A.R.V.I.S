"""
services/system_service.py — System Assistant + hardware health sentinel.

On start(): probes DB connectivity and ElevenLabs key validity.
mock_report() includes db_ok / voice_ok flags so the HUD SYS ASSIST chip
goes green once both subsystems are confirmed armed.
"""

from __future__ import annotations

import os
import random
from typing import Any

from sqlalchemy import text

from db.session import get_db
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

_STARTUP_MSG = "Memory Core online. Voice engine primed. Ready for May 16th, Sir."


class SystemService(BaseService):

    def __init__(self, dispatcher, state) -> None:
        super().__init__(dispatcher, state)
        self._db_ok:    bool = False
        self._voice_ok: bool = False
        self._startup_broadcast: bool = False

    @property
    def name(self) -> str:
        return "system_service"

    @property
    def display_name(self) -> str:
        return "System Assistant"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._db_ok    = self._check_db()
        self._voice_ok = self._check_voice()
        self._mark_online()

        if self._db_ok and self._voice_ok:
            self._log("All subsystems armed — DB ✓ Voice ✓")
        else:
            if not self._db_ok:    self._log("DB check failed",          "warning")
            if not self._voice_ok: self._log("ElevenLabs key not set",   "warning")

    async def stop(self) -> None:
        self._mark_offline()

    # ── Private health checks ─────────────────────────────────────────────────

    def _check_db(self) -> bool:
        try:
            with get_db() as db:
                db.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            self._log(f"DB probe: {exc}", "warning")
            return False

    def _check_voice(self) -> bool:
        key = os.getenv("ELEVENLABS_API_KEY", "")
        return key.startswith("sk_")

    # ── Mock API surface ──────────────────────────────────────────────────────

    def get_code_review(self, filepath: str = "unknown") -> dict[str, Any]:
        return {
            "file":       filepath,
            "issues":     random.randint(0, 4),
            "suggestion": random.choice(_SUGGESTIONS),
            "complexity": random.choice(["Low", "Medium", "High"]),
            "language":   random.choice(_LANGUAGES),
        }

    def get_automations(self) -> dict[str, Any]:
        return {
            "active_count": random.randint(2, 6),
            "runs_today":   random.randint(0, 8),
            "next_trigger": random.choice(_AUTOMATIONS),
            "errors":       random.randint(0, 1),
        }

    def get_dependency_scan(self) -> dict[str, Any]:
        return {
            "packages_checked": random.randint(12, 30),
            "outdated":         random.randint(0, 5),
            "vulnerabilities":  random.randint(0, 2),
            "last_scan":        "today",
        }

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        auto = self.get_automations()
        scan = self.get_dependency_scan()

        data: dict[str, Any] = {
            "automations":    auto["active_count"],
            "runs_today":     auto["runs_today"],
            "errors":         auto["errors"],
            "vulnerabilities": scan["vulnerabilities"],
            "outdated_pkgs":  scan["outdated"],
            "db_ok":          self._db_ok,
            "voice_ok":       self._voice_ok,
        }

        # Deliver startup message exactly once after first broadcast
        if self._db_ok and self._voice_ok and not self._startup_broadcast:
            data["startup_msg"] = _STARTUP_MSG
            self._startup_broadcast = True

        return {"type": "sysassist_update", "data": data}
