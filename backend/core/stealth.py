"""
core/stealth.py — Context-aware output suppression & location routing.

Decision tree:
  location in STEALTH_ZONES         → SILENT  (vibrate only)
  focus_mode == sleep               → SILENT
  focus_mode in (dnd, work)         → QUIET   (text only)
  airpods_connected                 → VOICE_PHONE
  giga_genie_online AND home        → VOICE_GENIE
  default                           → VOICE_PHONE
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from math import asin, cos, radians, sin, sqrt


class OutputMode(str, Enum):
    VOICE_PHONE = "voice_phone"   # TTS → phone speaker / AirPods
    VOICE_GENIE = "voice_genie"   # TTS → GiGA Genie 3
    QUIET       = "quiet"         # text notification, no voice
    SILENT      = "silent"        # haptic/vibrate only


class FocusMode(str, Enum):
    NONE  = "none"
    DND   = "dnd"
    SLEEP = "sleep"
    WORK  = "work"


# ── Geofence config (from .env) ───────────────────────────────────────────────

def _coord(key: str) -> float | None:
    v = os.getenv(key, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


GEOFENCES: dict[str, dict] = {
    "home":    {"lat": _coord("HOME_LAT"),    "lon": _coord("HOME_LON"),    "r": 100},
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
) -> RouteDecision:
    """Pure function — no side effects. Returns routing decision."""

    if location in STEALTH_ZONES:
        return RouteDecision(OutputMode.SILENT, f"stealth zone: {location}")

    if focus_mode == FocusMode.SLEEP:
        return RouteDecision(OutputMode.SILENT, "sleep mode")

    if focus_mode in (FocusMode.DND, FocusMode.WORK):
        return RouteDecision(OutputMode.QUIET, f"focus mode: {focus_mode.value}")

    if airpods_connected:
        return RouteDecision(OutputMode.VOICE_PHONE, "AirPods connected")

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
