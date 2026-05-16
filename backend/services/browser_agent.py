"""
services/browser_agent.py — Web content fetcher for proactive briefings.

Primary:  httpx (always available) — fetches and strips HTML to plain text.
Optional: Playwright (if installed) — handles JS-rendered pages.

Used by ProactiveAgent to gather context before generating LLM briefings.
"""
from __future__ import annotations

import re
import asyncio
import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def _strip_html(html: str, max_chars: int = 1500) -> str:
    """Remove tags and collapse whitespace. Returns plain text."""
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-z]+;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


async def fetch_page_text(url: str, use_playwright: bool = False) -> str:
    """
    Fetch a URL and return readable plain text.
    Falls back from Playwright → httpx gracefully.
    """
    if use_playwright:
        result = await _playwright_fetch(url)
        if result:
            return result

    return await _httpx_fetch(url)


async def _httpx_fetch(url: str) -> str:
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=10,
                                      follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.is_success:
                return _strip_html(resp.text)
    except Exception as exc:
        print(f"[BrowserAgent] httpx error for {url}: {exc}")
    return ""


async def _playwright_fetch(url: str) -> str:
    """Playwright headless fetch — returns empty string if not installed."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page    = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=15000)
            text    = await page.inner_text("body")
            await browser.close()
            return text[:1500]
    except ImportError:
        return ""
    except Exception as exc:
        print(f"[BrowserAgent] Playwright error for {url}: {exc}")
        return ""


async def fetch_morning_context() -> dict[str, str]:
    """
    Aggregate context for the morning briefing:
    weather + meal (via internal JARVIS API) + system snapshot.
    """
    results = await asyncio.gather(
        _httpx_jarvis("/weather"),
        _httpx_jarvis("/school/meal"),
        _httpx_jarvis("/telemetry"),
        return_exceptions=True,
    )
    weather_d, meal_d, tele_d = results

    weather = "날씨 정보 없음"
    if isinstance(weather_d, dict):
        weather = (
            f"{weather_d.get('condition', '?')} "
            f"현재 {weather_d.get('temp_c', '?')}도 "
            f"최고 {weather_d.get('max_c', '?')}도"
        )

    meal = "급식 정보 없음"
    if isinstance(meal_d, dict):
        items = meal_d.get("menu", [])
        if items:
            meal = ", ".join(items[:5])

    system = "시스템 정상"
    if isinstance(tele_d, dict):
        cpu = tele_d.get("cpu_percent", "?")
        bat = tele_d.get("battery_percent", "?")
        system = f"CPU {cpu}%, 배터리 {bat}%"

    return {"weather": weather, "meal": meal, "system": system}


async def _httpx_jarvis(path: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://127.0.0.1:8000{path}")
            return resp.json() if resp.is_success else {}
    except Exception:
        return {}
