"""
client/predictive_alert.py — Context-Aware Tardiness Detector.

Compares upcoming calendar events with current GPS position and fires an
alert when the user needs to leave NOW to arrive on time.

Location sources (priority order):
  1. WS push from phone: {"type": "location_update", "lat": ..., "lon": ...}
  2. IP-based geolocation fallback  →  ip-api.com (stdlib urllib, no extra dep)

Known locations are resolved from KNOWN_LOCATIONS dict.  If the event's
location field matches a key, haversine distance is computed and converted
to travel time at TRAVEL_SPEED_KMH.

Alert threshold:
  time_until_event < (travel_minutes + ALERT_BUFFER_MIN)  →  fire once per event

The alert is POSTed to /alert on the backend, which broadcasts as a
proactive_alert WS event — visible on both the browser HUD and the overlay.

Configuring KNOWN_LOCATIONS:
  Edit the dict below with actual coordinates.  Keys must match the "location"
  field of calendar events exactly.

GPS update from mobile app (add to frontend/mobile/App.js):
  ws.send(JSON.stringify({type:"location_update", lat: coords.latitude, lon: coords.longitude}))
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("jarvis.predictive_alert")

# ── Configuration ─────────────────────────────────────────────────────────────
ALERT_BUFFER_MIN  = 15.0   # depart this many minutes early as buffer
TRAVEL_SPEED_KMH  = 35.0   # assumed average travel speed (city + wait time)
CHECK_INTERVAL_S  = 120    # check tardiness risk every 2 minutes

# Replace values with actual lat/lon for each location
KNOWN_LOCATIONS: dict[str, tuple[float, float]] = {
    "학교":         (37.5665, 126.9780),
    "기숙사":       (37.5650, 126.9760),
    "도서관":       (37.5670, 126.9785),
    "강의실":       (37.5668, 126.9783),
    "school":       (37.5665, 126.9780),
    "dormitory":    (37.5650, 126.9760),
}

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class _CalEvent:
    title:    str
    location: str
    time_str: str           # "HH:MM" from calendar_data
    epoch:    float = field(init=False)

    def __post_init__(self) -> None:
        import datetime
        self.epoch = 0.0
        try:
            parts = self.time_str.split(":")
            h, m = int(parts[0]), int(parts[1])
            today = datetime.date.today()
            self.epoch = datetime.datetime.combine(today, datetime.time(h, m)).timestamp()
        except (ValueError, IndexError):
            pass


# ── Core class ────────────────────────────────────────────────────────────────

class PredictiveAlert:
    """
    Runs as a background asyncio Task inside the WS receiver thread.

    The WS receive loop feeds it data:
      predictor.update_location(lat, lon)   # from location_update WS event
      predictor.update_events(events_list)  # from calendar_data WS event
    """

    def __init__(self, http_base: str) -> None:
        self._http_base = http_base.rstrip("/")
        self._pos:    tuple[float, float] | None = None
        self._events: list[_CalEvent] = []
        self._fired:  set[str] = set()   # keys of already-fired alerts this session

    # ── Public setters (called from WS receive loop) ──────────────────────────

    def update_location(self, lat: float, lon: float) -> None:
        self._pos = (lat, lon)
        log.debug("[Predict] GPS updated → %.4f, %.4f", lat, lon)

    def update_events(self, raw: list[dict]) -> None:
        self._events = [
            _CalEvent(
                title=e.get("title", ""),
                location=e.get("location", ""),
                time_str=e.get("time", ""),
            )
            for e in raw
        ]
        self._fired.clear()
        log.debug("[Predict] %d events loaded.", len(self._events))

    # ── Background loop ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """Entry point — launch as asyncio.create_task(predictor.run())."""
        await self._ip_geolocate()
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)
            await self._check_all()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _ip_geolocate(self) -> None:
        """Fallback: resolve approximate position from public IP."""
        if self._pos is not None:
            return
        try:
            loop = asyncio.get_event_loop()
            raw  = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(
                    "http://ip-api.com/json/?fields=status,lat,lon", timeout=5
                ).read(),
            )
            data = json.loads(raw)
            if data.get("status") == "success":
                self._pos = (float(data["lat"]), float(data["lon"]))
                log.info("[Predict] IP geoloc: %.4f, %.4f", *self._pos)
        except Exception as exc:
            log.warning("[Predict] IP geoloc failed: %s", exc)

    async def _check_all(self) -> None:
        if not self._pos or not self._events:
            return
        now = time.time()
        for ev in self._events:
            if ev.epoch <= now:
                continue
            await self._check_one(ev, now)

    async def _check_one(self, ev: _CalEvent, now: float) -> None:
        minutes_until = (ev.epoch - now) / 60.0
        travel = self._travel_minutes(ev.location)
        if travel is None:
            return

        threshold = travel + ALERT_BUFFER_MIN
        key       = f"{ev.title}|{ev.time_str}"

        if minutes_until <= threshold and key not in self._fired:
            self._fired.add(key)
            late_risk = minutes_until < travel
            if late_risk:
                msg = (
                    f"🚨 지각 위험 — '{ev.title}' ({ev.time_str}) 까지 "
                    f"이동 {travel:.0f}분 필요, {minutes_until:.0f}분 남음. 즉시 출발하세요!"
                )
            else:
                msg = (
                    f"⏰ 출발 시각 — '{ev.title}' ({ev.time_str}) 까지 "
                    f"이동 {travel:.0f}분. {minutes_until:.0f}분 후 출발 권고."
                )
            log.warning("[Predict] %s", msg)
            asyncio.create_task(self._send_alert(msg))

    def _travel_minutes(self, location: str) -> float | None:
        if not location or not self._pos:
            return None
        dest = KNOWN_LOCATIONS.get(location)
        if dest is None:
            return None
        dist_km = _haversine(self._pos, dest)
        return (dist_km / TRAVEL_SPEED_KMH) * 60.0

    async def _send_alert(self, message: str) -> None:
        import aiohttp  # lazy
        try:
            async with aiohttp.ClientSession() as sess:
                await sess.post(
                    f"{self._http_base}/alert",
                    json={"message": message, "severity": "HIGH"},
                    timeout=aiohttp.ClientTimeout(total=6),
                )
        except Exception as exc:
            log.warning("[Predict] Alert send failed: %s", exc)


# ── Haversine ─────────────────────────────────────────────────────────────────

def _haversine(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))
