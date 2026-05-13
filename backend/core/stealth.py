"""
core/stealth.py — Context-aware output suppression & location routing.

Priority order (highest → lowest):
  1. Dormitory geofence                                  → STANDBY (full pause)
  2. Academy class in session (KST time schedule)        → SILENT
  3. GPS in STEALTH_ZONES (academy / library geofence)   → SILENT
  4. focus_mode == sleep                                 → SILENT
  5. focus_mode in (dnd, work)                           → QUIET
  6. AirPods connected                                   → VOICE_PHONE
  7. GiGA Genie online + home                            → VOICE_GENIE
  8. Default                                             → VOICE_PHONE
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from enum import Enum
from math import asin, cos, radians, sin, sqrt

try:
    from zoneinfo import ZoneInfo as _ZI
    _KST = _ZI("Asia/Seoul")
except Exception:
    from datetime import timezone, timedelta
    _KST = timezone(timedelta(hours=9))   # type: ignore[assignment]


class OutputMode(str, Enum):
    VOICE_PHONE = "voice_phone"   # TTS → phone speaker / AirPods
    VOICE_GENIE = "voice_genie"   # TTS → GiGA Genie 3
    QUIET       = "quiet"         # silent text notification
    SILENT      = "silent"        # haptic / vibrate only
    STANDBY     = "standby"       # dormitory — all output paused


class FocusMode(str, Enum):
    NONE  = "none"
    DND   = "dnd"
    SLEEP = "sleep"
    WORK  = "work"


# ── Academy schedule (KST, repeating weekly) ─────────────────────────────────
# (day_of_week, start, end, label)  0=Mon … 4=Fri 5=Sat 6=Sun

_ACADEMY_HOURS: tuple[tuple[int, dtime, dtime, str], ...] = (
    (4, dtime(19, 40), dtime(21, 20), "math_fri"),      # Friday math
    (5, dtime(9,  30), dtime(12, 10), "english_sat"),   # Saturday English
    (5, dtime(13, 30), dtime(16,  0), "math_sat"),      # Saturday Math
    (6, dtime(14, 30), dtime(16, 30), "math_sun"),      # Sunday Math (optional)
)


def is_academy_hour(dt: datetime | None = None) -> bool:
    """True if current KST time falls within a scheduled academy class."""
    now = dt or datetime.now(_KST)
    dow = now.weekday()
    t   = now.time().replace(second=0, microsecond=0)
    return any(dow == d and s <= t <= e for d, s, e, _ in _ACADEMY_HOURS)


def current_academy_session(dt: datetime | None = None) -> str | None:
    """Returns session label if in an academy hour, else None."""
    now = dt or datetime.now(_KST)
    dow = now.weekday()
    t   = now.time().replace(second=0, microsecond=0)
    for d, s, e, label in _ACADEMY_HOURS:
        if dow == d and s <= t <= e:
            return label
    return None


_QUIET_START = dtime(0, 0)
_QUIET_END   = dtime(7, 0)


def is_quiet_hours(dt: datetime | None = None) -> bool:
    """True if KST time is 00:00–07:00: no proactive voice, visual overlay only."""
    now = dt or datetime.now(_KST)
    t   = now.time().replace(second=0, microsecond=0)
    return _QUIET_START <= t < _QUIET_END


def current_mute_state(dt: datetime | None = None) -> dict:
    """Quick snapshot for /mute/status endpoint and stealth_update broadcasts."""
    academy  = is_academy_hour(dt)
    quiet    = is_quiet_hours(dt)
    session  = current_academy_session(dt)
    muted    = academy or quiet
    if academy:
        reason = f"수업 중 — {session}"
        icon   = "📚"
    elif quiet:
        reason = "심야 모드 (00:00–07:00)"
        icon   = "🌙"
    else:
        reason = ""
        icon   = ""
    return {
        "muted":          muted,
        "academy_hour":   academy,
        "quiet_hours":    quiet,
        "session":        session,
        "reason":         reason,
        "icon":           icon,
    }


def is_weekend_hyperfocus(dt: datetime | None = None) -> bool:
    """
    True during the weekend high-intensity window:
    Friday 19:40 → end of Sunday (highest Fury sensitivity period).
    """
    now = dt or datetime.now(_KST)
    dow = now.weekday()
    t   = now.time()
    if dow == 4 and t >= dtime(19, 40):  # Friday from 19:40
        return True
    if dow == 5:                          # All of Saturday
        return True
    if dow == 6:                          # All of Sunday
        return True
    return False


# ── Geofence config (from .env) ───────────────────────────────────────────────

def _coord(key: str) -> float | None:
    v = os.getenv(key, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


GEOFENCES: dict[str, dict] = {
    "home":    {"lat": _coord("HOME_LAT"),    "lon": _coord("HOME_LON"),    "r": 100},
    "dorm":    {"lat": _coord("DORM_LAT"),    "lon": _coord("DORM_LON"),    "r": 150},
    "academy": {"lat": _coord("ACADEMY_LAT"), "lon": _coord("ACADEMY_LON"), "r": 80},
    "library": {"lat": _coord("LIBRARY_LAT"), "lon": _coord("LIBRARY_LON"), "r": 80},
}

STEALTH_ZONES: frozenset[str] = frozenset({"academy", "library"})


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    φ1, φ2 = radians(lat1), radians(lat2)
    dφ, dλ = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dφ / 2) ** 2 + cos(φ1) * cos(φ2) * sin(dλ / 2) ** 2
    return 2 * R * asin(sqrt(a))


def classify_location(lat: float, lon: float) -> str:
    for zone, cfg in GEOFENCES.items():
        if cfg["lat"] is None:
            continue
        if _haversine_m(lat, lon, cfg["lat"], cfg["lon"]) <= cfg["r"]:
            return zone
    return "unknown"


# ── Routing decision ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RouteDecision:
    mode:   OutputMode
    reason: str


def decide_output(
    location: str           = "unknown",
    focus_mode: FocusMode   = FocusMode.NONE,
    airpods_connected: bool = False,
    giga_genie_online: bool = False,
    dt: datetime | None     = None,
) -> RouteDecision:
    """Pure function — no side effects. Returns routing decision."""

    # ① Dormitory → full standby (highest priority)
    if location == "dorm":
        return RouteDecision(OutputMode.STANDBY, "dormitory — standby mode")

    # ② Academy schedule (time-based KST)
    session = current_academy_session(dt)
    if session:
        return RouteDecision(OutputMode.SILENT, f"academy class: {session}")

    # ③ GPS stealth zones
    if location in STEALTH_ZONES:
        return RouteDecision(OutputMode.SILENT, f"stealth zone: {location}")

    # ④ Sleep mode
    if focus_mode == FocusMode.SLEEP:
        return RouteDecision(OutputMode.SILENT, "sleep mode")

    # ④b Late-night silence (00:00–07:00 KST) — visual overlay only
    if is_quiet_hours(dt):
        return RouteDecision(OutputMode.SILENT, "quiet hours: 00:00–07:00")

    # ⑤ DND / Work focus
    if focus_mode in (FocusMode.DND, FocusMode.WORK):
        return RouteDecision(OutputMode.QUIET, f"focus mode: {focus_mode.value}")

    # ⑥ AirPods
    if airpods_connected:
        return RouteDecision(OutputMode.VOICE_PHONE, "AirPods connected")

    # ⑦ GiGA Genie at home
    if giga_genie_online and location == "home":
        return RouteDecision(OutputMode.VOICE_GENIE, "home + GiGA Genie online")

    return RouteDecision(OutputMode.VOICE_PHONE, "default")


# ── Welcome Home ──────────────────────────────────────────────────────────────

_WELCOME: dict[str, str] = {
    "partner": "Welcome home, darling. I've been keeping everything running smoothly.",
    "sir":     "Welcome back, Sir. All systems nominal. Shall I run the evening brief?",
}


def welcome_home_phrase(person: str = "sir") -> str:
    return _WELCOME.get(person, _WELCOME["sir"])
