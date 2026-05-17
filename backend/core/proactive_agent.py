"""
core/proactive_agent.py — Time-based proactive briefing engine.

Runs as an asyncio background task. Every 60 s it checks whether any
trigger condition is met and, if so, calls llm.generate() with a
TTS-safe system prompt and delivers the result via _push_fn (injected
by main.py).

Trigger schedule (KST):
  07:30  Morning briefing — weather + today's highlights
  12:00  Lunch reminder  — school meal summary (weekdays)
  16:00  Study nudge     — weakest subject reminder (weekdays)
  22:00  Night wrap      — day-end system status
  Any    Pre-event       — 30 min before calendar events
  Any    Battery         — laptop battery < 20 % unplugged
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Callable, Awaitable

import httpx

import core.llm_client as llm

KST = timezone(timedelta(hours=9))

# ── TTS-safe system prompt ────────────────────────────────────────────────────
_TTS_SYSTEM = """\
You are J.A.R.V.I.S., an ultra-advanced AI assistant speaking aloud to your Creator.
Generate ONLY natural spoken Korean — the exact words that should come out of a speaker.

ABSOLUTE RULES (violating any one ruins the audio):
• Zero markdown: no *, **, #, -, >, `, ~, or any symbol used for formatting.
• Zero emojis. They sound like gibberish when read by TTS.
• Zero bullet lists. Write flowing sentences.
• Maximum 3 sentences per message. Concise and purposeful.
• Always end with ", Sir" or ", Boss". This is non-negotiable.
• Tone: calm, measured British gentleman — like a trusted advisor who has already handled things.
• Never say you are an AI, mention API keys, or break character in any way.
"""


def _now_kst() -> datetime:
    return datetime.now(KST)


class ProactiveAgent:
    """
    Watches the clock and context; speaks first when the moment is right.
    Injected via main.py: push_fn(text) broadcasts text + TTS to all clients.
    """

    def __init__(self, push_fn: Callable[[str], Awaitable[None]]) -> None:
        self._push       = push_fn
        self._calendar:  list[dict] = []
        self._cal_date:  str        = ""
        self._fired:     set[str]   = set()   # "trigger:YYYY-MM-DD[:HH]"
        self._last_bat_warn: float  = 0.0     # monotonic time of last battery warn

    # ── Calendar feed (called by main.py WS handler) ──────────────────────────

    def set_calendar(self, events: list[dict], date: str = "") -> None:
        self._calendar = events
        self._cal_date = date

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Background task: check triggers every 60 seconds."""
        await asyncio.sleep(10)   # brief delay to let server fully start
        while True:
            try:
                await self._tick()
            except Exception as exc:
                print(f"[Proactive] tick error: {exc}")
            await asyncio.sleep(60)

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        now   = _now_kst()
        today = now.strftime("%Y-%m-%d")
        hm    = (now.hour, now.minute)

        # Morning briefing: 07:30–07:34
        if (7, 30) <= hm <= (7, 34):
            await self._once("morning", today, self._morning_briefing, now)

        # Lunch reminder: 12:00–12:04 (weekdays)
        if (12, 0) <= hm <= (12, 4) and now.weekday() < 5:
            await self._once("lunch", today, self._lunch_briefing, now)

        # Study nudge: 16:00–16:04 (weekdays)
        if (16, 0) <= hm <= (16, 4) and now.weekday() < 5:
            await self._once("study", today, self._study_nudge, now)

        # Night wrap: 22:00–22:04
        if (22, 0) <= hm <= (22, 4):
            await self._once("night", today, self._night_wrap, now)

        # Pre-event warnings (checked every tick)
        await self._pre_event_check(now, today)

        # Battery warning (throttled to once per 30 min)
        await self._battery_check()

    # ── Once-per-period guard ─────────────────────────────────────────────────

    async def _once(self, name: str, date: str, fn, *args) -> None:
        key = f"{name}:{date}"
        if key in self._fired:
            return
        self._fired.add(key)
        await fn(*args)

    # ── Trigger implementations ───────────────────────────────────────────────

    async def _morning_briefing(self, now: datetime) -> None:
        weather = await _fetch_weather()
        cal_str = _format_calendar(self._calendar) or "오늘 등록된 일정이 없습니다"
        prompt  = (
            f"현재 시각은 {now.strftime('%H시 %M분')}입니다. "
            f"날씨 정보: {weather}. "
            f"오늘 일정: {cal_str}. "
            "사용자에게 상쾌한 아침 브리핑을 전해줘. "
            "날씨와 오늘의 핵심 일정을 언급해줘."
        )
        text = await _generate(prompt)
        await self._push(text)

    async def _lunch_briefing(self, now: datetime) -> None:
        meal = await _fetch_meal()
        prompt = (
            f"점심 시간입니다. 오늘 급식 메뉴: {meal}. "
            "자비스답게 점심을 챙기라고 부드럽게 말을 걸어줘."
        )
        text = await _generate(prompt)
        await self._push(text)

    async def _study_nudge(self, now: datetime) -> None:
        weak_subject = await _fetch_weakest_subject()
        subject_hint = f"특히 '{weak_subject}' 과목이 약점으로 등록되어 있어 우선 집중 권장." if weak_subject else ""
        prompt = (
            f"현재 시각은 오후 {now.strftime('%H시 %M분')}입니다. "
            f"{subject_hint} "
            "학습 집중 시간을 알리는 부드럽고 단호한 한마디를 해줘. "
            "포모도로 25분 세션 시작을 권유해줘."
        )
        text = await _generate(prompt)
        await self._push(text)

    async def _night_wrap(self, now: datetime) -> None:
        from core.device_context import device_ctx
        laptop = device_ctx._laptop
        cpu    = laptop.get("cpu", "?")
        ram    = laptop.get("ram", "?")
        prompt = (
            f"현재 시각은 밤 {now.strftime('%H시 %M분')}입니다. "
            f"노트북 상태 — CPU {cpu}%, RAM {ram}%. "
            "하루를 마무리하는 간결하고 품격 있는 저녁 마무리 인사를 해줘. "
            "내일을 위한 간단한 한 가지 조언도 포함해줘."
        )
        text = await _generate(prompt)
        await self._push(text)

    async def _pre_event_check(self, now: datetime, today: str) -> None:
        for event in self._calendar:
            time_str = event.get("time", "")
            title    = event.get("title", "")
            if not time_str or not title:
                continue
            try:
                h, m = map(int, time_str.split(":"))
                event_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff_min = int((event_dt - now).total_seconds() / 60)
                if 28 <= diff_min <= 32:
                    key = f"preevent:{today}:{time_str}"
                    if key not in self._fired:
                        self._fired.add(key)
                        prompt = (
                            f"30분 후에 '{title}' 일정이 시작됩니다. "
                            "사용자에게 준비하도록 알려줘. 시간과 일정명을 반드시 언급해."
                        )
                        text = await _generate(prompt)
                        await self._push(text)
            except ValueError:
                continue

    async def _battery_check(self) -> None:
        import time as _time
        from core.device_context import device_ctx
        laptop = device_ctx._laptop
        bat    = laptop.get("battery", 100)
        plug   = laptop.get("charging", True)
        if bat < 20 and not plug:
            now = _time.monotonic()
            if now - self._last_bat_warn > 1800:   # warn at most once per 30 min
                self._last_bat_warn = now
                prompt = (
                    f"노트북 배터리가 {bat}%로 위험 수준입니다. 충전기를 연결하지 않은 상태입니다. "
                    "즉시 충전을 권고하는 짧고 단호한 경고를 해줘."
                )
                text = await _generate(prompt)
                await self._push(text)


# ── LLM helper ────────────────────────────────────────────────────────────────

async def _generate(user_prompt: str) -> str:
    """Call LLM with TTS-safe system prompt. Strips any residual markdown."""
    if llm.SIMULATION_MODE:
        return "현재 신경 링크가 시뮬레이션 모드입니다. API 키를 설정해 주시면 즉시 활성화됩니다, Sir."
    try:
        raw = await asyncio.to_thread(
            llm.generate,
            [{"role": "user", "content": user_prompt}],
            system=_TTS_SYSTEM,
            max_tokens=200,
        )
        return _strip_markdown(raw)
    except Exception as exc:
        print(f"[Proactive] LLM error: {exc}")
        return "잠시 신경 링크에 지연이 발생했습니다. 곧 복구될 예정입니다, Sir."


def _strip_markdown(text: str) -> str:
    """Remove markdown symbols that break TTS."""
    import re
    text = re.sub(r'[*_#`~>]', '', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


# ── Data fetchers ─────────────────────────────────────────────────────────────

async def _fetch_weather() -> str:
    """Fetch current weather from the JARVIS /weather endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://127.0.0.1:8000/weather")
            if resp.is_success:
                d    = resp.json()
                temp = d.get("temp_c") or d.get("temp", "?")
                hi   = d.get("max_c", "")
                lo   = d.get("min_c", "")
                cond = d.get("condition", "알 수 없음")
                parts = [f"{cond}, 현재 {temp}도"]
                if hi:
                    parts.append(f"최고 {hi}도")
                if lo:
                    parts.append(f"최저 {lo}도")
                return ", ".join(parts)
    except Exception:
        pass
    return "날씨 정보를 가져오지 못했습니다"


async def _fetch_meal() -> str:
    """Fetch today's school meal from the JARVIS /school/meal endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://127.0.0.1:8000/school/meal")
            if resp.is_success:
                d    = resp.json()
                items = d.get("menu", [])
                if items:
                    return ", ".join(items[:5])
    except Exception:
        pass
    return "급식 정보를 가져오지 못했습니다"


def _format_calendar(events: list[dict]) -> str:
    if not events:
        return ""
    parts = [f"{e.get('time', '')} {e.get('title', '')}" for e in events[:4]]
    return ", ".join(p.strip() for p in parts if p.strip())


async def _fetch_weakest_subject() -> str:
    """Fetch the highest-priority weakness from the study plan DB."""
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.get("http://127.0.0.1:8000/study/plan?status=pending")
            if resp.is_success:
                plans = resp.json().get("plans", [])
                if plans:
                    top = plans[0]  # already ordered by weakness_level desc
                    return f"{top.get('subject','')} — {top.get('topic','')}"
    except Exception:
        pass
    return ""
