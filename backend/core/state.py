"""
core/state.py — Singleton global state manager.

Single source of truth for all mutable runtime state.
Thread-safe via Lock; async-safe because mutations are atomic Python ops.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class _State:
    """All mutable runtime state lives here — never scattered as module globals."""

    # ── Reactor / Energy ──────────────────────────────────────────────────────
    energy_saving: bool = False

    # ── Session counters ──────────────────────────────────────────────────────
    message_count: int = 0
    deploy_count:  int = 0

    # ── Active service flags ──────────────────────────────────────────────────
    services_online: set[str] = field(default_factory=set)

    # ── Dopamine guard ────────────────────────────────────────────────────────
    dopamine_guard_active: bool = False
    focus_session_minutes: int  = 25   # Pomodoro default

    # ── Memory service ────────────────────────────────────────────────────────
    memory_backend: str = "local"      # "local" | "chroma" | "pinecone"

    # ── Daily study stats (resets at midnight) ────────────────────────────────
    study_sessions_today:     int  = 0
    study_minutes_today:      int  = 0
    study_distractions_today: int  = 0
    study_last_reset:         str  = ""   # ISO date string YYYY-MM-DD


class StateManager:
    """Thread-safe Singleton wrapping _State."""

    _instance: StateManager | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "StateManager":
        with cls._lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._s = _State()
                cls._instance = obj
        return cls._instance

    # ── Energy / Reactor ──────────────────────────────────────────────────────

    @property
    def energy_saving(self) -> bool:
        """True → slow poll, static HUD animations."""
        return self._s.energy_saving

    @energy_saving.setter
    def energy_saving(self, value: bool) -> None:
        self._s.energy_saving = value

    # ── Counters ──────────────────────────────────────────────────────────────

    @property
    def message_count(self) -> int:
        return self._s.message_count

    def increment_messages(self) -> int:
        self._s.message_count += 1
        return self._s.message_count

    @property
    def deploy_count(self) -> int:
        return self._s.deploy_count

    def increment_deploys(self) -> int:
        self._s.deploy_count += 1
        return self._s.deploy_count

    # ── Service registry ──────────────────────────────────────────────────────

    def register_service(self, name: str) -> None:
        """Mark a service as online."""
        self._s.services_online.add(name)

    def unregister_service(self, name: str) -> None:
        self._s.services_online.discard(name)

    @property
    def services_online(self) -> set[str]:
        return frozenset(self._s.services_online)

    # ── Dopamine guard ────────────────────────────────────────────────────────

    @property
    def dopamine_guard_active(self) -> bool:
        return self._s.dopamine_guard_active

    @dopamine_guard_active.setter
    def dopamine_guard_active(self, value: bool) -> None:
        self._s.dopamine_guard_active = value

    @property
    def focus_session_minutes(self) -> int:
        return self._s.focus_session_minutes

    @focus_session_minutes.setter
    def focus_session_minutes(self, minutes: int) -> None:
        self._s.focus_session_minutes = max(1, minutes)

    # ── Memory backend ────────────────────────────────────────────────────────

    @property
    def memory_backend(self) -> str:
        return self._s.memory_backend

    @memory_backend.setter
    def memory_backend(self, backend: str) -> None:
        self._s.memory_backend = backend

    # ── Daily study stats ─────────────────────────────────────────────────────

    def _maybe_reset_study(self) -> None:
        today = date.today().isoformat()
        if self._s.study_last_reset != today:
            self._s.study_sessions_today     = 0
            self._s.study_minutes_today      = 0
            self._s.study_distractions_today = 0
            self._s.study_last_reset         = today

    def record_focus_session(self, minutes: int, distractions: int = 0) -> None:
        """Called by DopamineGuard on session completion."""
        self._maybe_reset_study()
        self._s.study_sessions_today     += 1
        self._s.study_minutes_today      += minutes
        self._s.study_distractions_today += distractions

    def daily_study_stats(self) -> dict:
        self._maybe_reset_study()
        return {
            "sessions":     self._s.study_sessions_today,
            "minutes":      self._s.study_minutes_today,
            "distractions": self._s.study_distractions_today,
            "date":         self._s.study_last_reset,
        }

    # ── Snapshot ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of current state."""
        return {
            "energy_saving":        self._s.energy_saving,
            "message_count":        self._s.message_count,
            "deploy_count":         self._s.deploy_count,
            "services_online":      list(self._s.services_online),
            "dopamine_guard_active": self._s.dopamine_guard_active,
            "focus_session_minutes": self._s.focus_session_minutes,
            "memory_backend":       self._s.memory_backend,
            "daily_study":          self.daily_study_stats(),
        }


# Module-level singleton — import this everywhere
state = StateManager()
