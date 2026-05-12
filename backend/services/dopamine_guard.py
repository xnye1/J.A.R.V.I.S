"""
services/dopamine_guard.py — Focus & distraction process monitor.

Watches running processes via psutil and emits HUD alerts when
distraction patterns are detected during a declared focus session.

Focus session lifecycle:
  1. Client sends WS {"type": "focus_start", "minutes": 25}
  2. DopamineGuard starts tracking
  3. Every POLL_INTERVAL it scans process names
  4. If a distraction process is detected → HUD alert at HIGH priority
  5. Session ends after `minutes` or on explicit {"type": "focus_end"}

Distraction categories (configurable via DISTRACTION_PROFILES):
  "video"   — video streaming (youtube, netflix, vlc, mpv …)
  "social"  — social media browser patterns
  "gaming"  — Steam, game executables
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

from core.dispatcher import Priority
from core.anger_engine import anger
from services.base_service import BaseService

log = logging.getLogger("jarvis.dopamine_guard")

POLL_INTERVAL = 15       # seconds between process scans
GRACE_PERIOD  = 60       # seconds before first alert (avoid false positives)


# ── Distraction profiles ──────────────────────────────────────────────────────

DISTRACTION_PROFILES: dict[str, list[str]] = {
    "video": [
        "youtube", "netflix", "twitch", "vlc", "mpv",
        "plex", "disney", "wavve", "watcha",
    ],
    "social": [
        "twitter", "instagram", "facebook", "tiktok",
        "reddit", "discord", "slack",
    ],
    "gaming": [
        "steam", "epicgames", "battle.net", "leagueoflegends",
        "valorant", "minecraft",
    ],
}

# Flatten to a quick lookup set
_ALL_DISTRACTIONS: set[str] = {
    kw for kws in DISTRACTION_PROFILES.values() for kw in kws
}


# ── Session model ─────────────────────────────────────────────────────────────

@dataclass
class FocusSession:
    duration_minutes: int
    started_at:       float = field(default_factory=time.monotonic)
    alerts_sent:      int   = 0
    distractions_hit: list[str] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_expired(self) -> bool:
        return self.elapsed_seconds >= self.duration_minutes * 60

    @property
    def remaining_minutes(self) -> float:
        remaining = self.duration_minutes * 60 - self.elapsed_seconds
        return max(0.0, remaining / 60)


# ── Service ───────────────────────────────────────────────────────────────────

class DopamineGuard(BaseService):
    """
    Pomodoro-style focus session monitor.

    Exposes control methods that route handlers call in response to
    WS messages from the remote controller or HUD.
    """

    @property
    def name(self) -> str:
        return "dopamine_guard"

    @property
    def display_name(self) -> str:
        return "Dopamine Guard (Focus Monitor)"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session:  FocusSession | None = None
        self._task:     asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._mark_online()
        await self._emit(
            {"type": "service_online", "service": self.name},
            Priority.LOW,
        )

    async def stop(self) -> None:
        await self.end_session(notify=False)
        self._mark_offline()

    # ── Session control (called by route handlers) ────────────────────────────

    async def begin_session(self, minutes: int | None = None) -> FocusSession:
        """
        Start a focus session.

        Parameters
        ----------
        minutes : Session length. Defaults to state.focus_session_minutes.
        """
        dur = minutes or self._state.focus_session_minutes
        self._session = FocusSession(duration_minutes=dur)
        self._state.dopamine_guard_active = True

        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._monitor_loop())

        await self._emit({
            "type":    "dopamine_session_start",
            "minutes": dur,
            "message": f"Focus session active — {dur} min. Distractions will be flagged, Sir.",
        }, Priority.HIGH)

        self._log(f"Focus session started ({dur} min).")
        return self._session

    async def end_session(self, notify: bool = True) -> dict[str, Any] | None:
        """End the current focus session and return a summary."""
        if self._task and not self._task.done():
            self._task.cancel()

        if self._session is None:
            return None

        sess = self._session
        self._session = None
        self._state.dopamine_guard_active = False

        summary = {
            "duration_minutes":   sess.duration_minutes,
            "elapsed_seconds":    round(sess.elapsed_seconds),
            "alerts_sent":        sess.alerts_sent,
            "distractions_hit":   sess.distractions_hit,
        }

        if notify:
            await self._emit({
                "type":    "dopamine_session_end",
                "summary": summary,
                "message": (
                    f"Session complete. {sess.alerts_sent} distraction alerts fired, Sir. "
                    f"Elapsed: {round(sess.elapsed_seconds / 60, 1)} min."
                ),
            }, Priority.HIGH)

        self._log("Focus session ended.")
        return summary

    def session_status(self) -> dict[str, Any] | None:
        """Return live session stats or None if no session is active."""
        if self._session is None:
            return None
        return {
            "active":           True,
            "duration_minutes": self._session.duration_minutes,
            "remaining_minutes": round(self._session.remaining_minutes, 1),
            "alerts_sent":      self._session.alerts_sent,
        }

    # ── Internal monitor loop ─────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Poll running processes every POLL_INTERVAL seconds."""
        await asyncio.sleep(GRACE_PERIOD)   # grace period before first scan

        while self._session and not self._session.is_expired:
            detected = await asyncio.to_thread(self._scan_processes)
            if detected:
                await self._fire_distraction_alert(detected)
            await asyncio.sleep(POLL_INTERVAL)

        if self._session and self._session.is_expired:
            await self.end_session(notify=True)

    @staticmethod
    def _scan_processes() -> list[str]:
        """Return names of detected distraction processes (blocking, runs in thread)."""
        found: list[str] = []
        try:
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    name    = (proc.info.get("name") or "").lower()
                    cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                    text    = name + " " + cmdline
                    for kw in _ALL_DISTRACTIONS:
                        if kw in text:
                            found.append(kw)
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            log.debug("Process scan error: %s", exc)
        return list(set(found))

    async def _fire_distraction_alert(self, detected: list[str]) -> None:
        if self._session is None:
            return
        self._session.alerts_sent += 1
        self._session.distractions_hit.extend(detected)
        names = ", ".join(detected)
        remaining = round(self._session.remaining_minutes, 1)

        # Feed anger engine — every block raises the gauge
        anger.record_dopamine_block()

        await self._emit({
            "type":    "dopamine_alert",
            "message": (
                f"[DOPAMINE GUARD] Distraction detected: {names}. "
                f"{remaining} min remaining in focus session, Sir. Return to task."
            ),
            "detected":  detected,
            "remaining": remaining,
            "anger_gauge": anger.gauge,
            "anger_stage": anger.profile.name,
        }, Priority.HIGH)
