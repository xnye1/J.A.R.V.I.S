"""
services/school_service.py — School meal (NEIS) + weather (Open-Meteo).

Configuration (.env):
  NEIS_API_KEY      — 나이스 API 키 (open.neis.go.kr 발급)
  NEIS_OFFICE_CODE  — 시도교육청 코드 (e.g. "K10" = 경기도)
  NEIS_SCHOOL_CODE  — 학교 코드 (표준학교코드)
  HOME_LAT / HOME_LON — used for weather (reuses home geofence coords)

Meal data refreshes once at startup and again daily at 07:00 KST.
Weather data refreshes every mock_report cycle (~10 s).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import httpx

from services.base_service import BaseService

try:
    from zoneinfo import ZoneInfo as _ZI
    _KST = _ZI("Asia/Seoul")
except Exception:
    from datetime import timezone, timedelta
    _KST = timezone(timedelta(hours=9))   # type: ignore[assignment]

log = logging.getLogger("jarvis.school")

_NEIS_KEY    = os.getenv("NEIS_API_KEY", "")
_OFFICE_CODE = os.getenv("NEIS_OFFICE_CODE", "")
_SCHOOL_CODE = os.getenv("NEIS_SCHOOL_CODE", "")
_HOME_LAT    = os.getenv("HOME_LAT", "")
_HOME_LON    = os.getenv("HOME_LON", "")

_WMO_CODES: dict[int, str] = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "이슬비", 53: "이슬비", 55: "강한 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    95: "뇌우", 99: "강한 뇌우",
}


class SchoolService(BaseService):
    """Fetches school meal schedule and local weather for the HUD."""

    def __init__(self, dispatcher, state) -> None:
        super().__init__(dispatcher, state)
        self._meal_cache: dict  = {}
        self._weather_cache: dict = {}
        self._last_meal_date: str = ""

    @property
    def name(self) -> str:
        return "school_service"

    @property
    def display_name(self) -> str:
        return "School & Weather"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._mark_online()
        await self._refresh_meal()
        await self._refresh_weather()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Data fetchers ─────────────────────────────────────────────────────────

    async def _refresh_meal(self) -> None:
        today = datetime.now(_KST).strftime("%Y%m%d")
        if today == self._last_meal_date:
            return
        self._meal_cache = await self._fetch_meal(today)
        self._last_meal_date = today

    async def _fetch_meal(self, date_yyyymmdd: str) -> dict:
        if not _NEIS_KEY or not _OFFICE_CODE or not _SCHOOL_CODE:
            return {"date": date_yyyymmdd, "menu": ["급식 정보 미설정 (NEIS_API_KEY 필요)"], "calories": None}

        url = "https://open.neis.go.kr/hub/mealServiceDietInfo"
        params = {
            "KEY":           _NEIS_KEY,
            "Type":          "json",
            "ATPT_OFCDC_SC_CODE": _OFFICE_CODE,
            "SD_SCHUL_CODE": _SCHOOL_CODE,
            "MLSV_YMD":      date_yyyymmdd,
        }
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                rows = (
                    data.get("mealServiceDietInfo", [{}])[1]
                    .get("row", [])
                )
                if not rows:
                    return {"date": date_yyyymmdd, "menu": ["급식 없음"], "calories": None}
                row  = rows[0]
                menu = [m.strip() for m in row.get("DDISH_NM", "").split("<br/>") if m.strip()]
                kcal = row.get("CAL_INFO", "")
                return {"date": date_yyyymmdd, "menu": menu, "calories": kcal}
        except Exception as exc:
            log.warning("NEIS meal fetch failed: %s", exc)
            return {"date": date_yyyymmdd, "menu": ["급식 정보 로드 실패"], "calories": None}

    async def _refresh_weather(self) -> None:
        lat = _HOME_LAT or "37.5665"
        lon = _HOME_LON or "126.9780"
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,apparent_temperature,weathercode,windspeed_10m"
                f"&timezone=Asia%2FSeoul"
            )
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                cur  = resp.json().get("current", {})
                code = cur.get("weathercode", 0)
                self._weather_cache = {
                    "temp":       round(cur.get("temperature_2m", 0), 1),
                    "feels_like": round(cur.get("apparent_temperature", 0), 1),
                    "condition":  _WMO_CODES.get(code, f"코드 {code}"),
                    "wind_kmh":   round(cur.get("windspeed_10m", 0), 1),
                }
        except Exception as exc:
            log.debug("Weather fetch failed: %s", exc)

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        meal    = self._meal_cache
        weather = self._weather_cache

        menu_preview = " · ".join(meal.get("menu", [])[:3]) or "미정"
        temp         = weather.get("temp", "--")
        condition    = weather.get("condition", "--")
        feels        = weather.get("feels_like", "--")

        return {
            "type": "school_update",
            "data": {
                "meal_date":    meal.get("date", ""),
                "meal_preview": menu_preview,
                "meal_full":    meal.get("menu", []),
                "calories":     meal.get("calories", ""),
                "temp":         temp,
                "feels_like":   feels,
                "condition":    condition,
                "wind_kmh":     weather.get("wind_kmh", "--"),
            },
        }

    # ── Public helpers ────────────────────────────────────────────────────────

    async def get_today_meal(self) -> dict:
        await self._refresh_meal()
        return self._meal_cache

    async def get_weather(self) -> dict:
        await self._refresh_weather()
        return self._weather_cache
