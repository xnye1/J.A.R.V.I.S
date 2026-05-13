"""
client/search_agent.py — Async Web Search & Shopping Agent

Search backend (in priority order):
  1. SerpApi Google Search — set SERPAPI_KEY env var
  2. DuckDuckGo HTML lite  — free, no key, auto-selected when SERPAPI_KEY absent

Shopping backend (in priority order):
  1. Naver Shopping API — set NAVER_CLIENT_ID + NAVER_CLIENT_SECRET
  2. DuckDuckGo Shopping tab scrape + price regex pattern

Results are sorted:
  Web:      relevance (as returned)
  Shopping: price ASC → delivery_days ASC (lowest price + fastest ship first)

open_product(url): launches browser via xdg-open / open / start.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("jarvis.search_agent")

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False
    log.warning("[Search] beautifulsoup4 not installed — search disabled")

# ── API keys from environment ─────────────────────────────────────────────────
_SERPAPI_KEY        = os.getenv("SERPAPI_KEY", "")
_NAVER_CLIENT_ID    = os.getenv("NAVER_CLIENT_ID", "")
_NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

_PRICE_RE = re.compile(r"[₩$€£￥]?\s*([\d,]+(?:\.\d{1,2})?)\s*[₩$€£￥]?")
_DELIV_RE = re.compile(r"(\d+)\s*(?:day|일|영업일)", re.IGNORECASE)


@dataclass
class SearchResult:
    title:         str
    url:           str
    snippet:       str     = ""
    price:         Optional[float] = None   # KRW or native currency
    delivery_days: Optional[int]   = None   # estimated delivery
    image_url:     Optional[str]   = None
    source:        str             = ""

    @property
    def is_shopping(self) -> bool:
        return self.price is not None

    def price_str(self) -> str:
        if self.price is None:
            return ""
        if self.price >= 10_000:
            return f"₩{self.price:,.0f}"
        return f"${self.price:,.2f}"


class SearchAgent:
    """
    Async search and shopping agent.

    search(query)  → list[SearchResult]  sorted by relevance
    shop(keyword)  → list[SearchResult]  sorted by price ASC, delivery ASC
    open_product(url)  → opens browser (non-blocking)
    """

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not _HAS_AIOHTTP:
            return []
        try:
            if _SERPAPI_KEY:
                return await self._serpapi_search(query, max_results)
            return await self._ddg_search(query, max_results)
        except Exception as exc:
            log.warning("[Search] web search failed: %s", exc)
            return []

    async def shop(self, keyword: str, max_results: int = 6) -> list[SearchResult]:
        if not _HAS_AIOHTTP:
            return []
        try:
            if _NAVER_CLIENT_ID and _NAVER_CLIENT_SECRET:
                results = await self._naver_shop(keyword, max_results)
            else:
                results = await self._ddg_shop(keyword, max_results)
            # Sort: price ASC first, then delivery_days ASC
            results.sort(key=lambda r: (
                r.price         if r.price         is not None else float("inf"),
                r.delivery_days if r.delivery_days is not None else 99,
            ))
            return results[:max_results]
        except Exception as exc:
            log.warning("[Search] shopping failed: %s", exc)
            return []

    @staticmethod
    def open_product(url: str) -> None:
        """Open product URL in default browser (non-blocking)."""
        if not url:
            return
        for cmd in (["xdg-open"], ["open"], ["start"]):
            try:
                subprocess.Popen(
                    cmd + [url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("[Search] Opened browser → %s", url[:60])
                return
            except FileNotFoundError:
                continue
        log.warning("[Search] No browser launcher found for %s", url[:60])

    # ── SerpApi web search ────────────────────────────────────────────────────

    async def _serpapi_search(self, query: str, n: int) -> list[SearchResult]:
        params = {
            "q": query, "api_key": _SERPAPI_KEY,
            "engine": "google", "num": str(n),
        }
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                "https://serpapi.com/search",
                params=params,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                data = await resp.json()
        out: list[SearchResult] = []
        for item in data.get("organic_results", [])[:n]:
            out.append(SearchResult(
                title   = item.get("title", ""),
                url     = item.get("link", ""),
                snippet = item.get("snippet", ""),
                source  = "serpapi",
            ))
        return out

    # ── DuckDuckGo HTML lite ──────────────────────────────────────────────────

    async def _ddg_search(self, query: str, n: int) -> list[SearchResult]:
        if not _HAS_BS4:
            return []
        url = "https://lite.duckduckgo.com/lite/"
        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as sess:
            async with sess.get(
                url, params={"q": query},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                html = await resp.text()

        soup  = BeautifulSoup(html, "html.parser")
        links = soup.select("a.result-link")
        snips = soup.select(".result-snippet")
        out:  list[SearchResult] = []
        for i, link in enumerate(links[:n]):
            href    = str(link.get("href", ""))
            title   = link.get_text(strip=True)
            snippet = snips[i].get_text(strip=True) if i < len(snips) else ""
            out.append(SearchResult(title=title, url=href, snippet=snippet, source="ddg"))
        return out

    # ── Naver Shopping API ────────────────────────────────────────────────────

    async def _naver_shop(self, keyword: str, n: int) -> list[SearchResult]:
        headers = {
            "X-Naver-Client-Id":     _NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": _NAVER_CLIENT_SECRET,
        }
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get(
                "https://openapi.naver.com/v1/search/shop.json",
                params={"query": keyword, "display": str(n), "sort": "asc"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                data = await resp.json()
        out: list[SearchResult] = []
        for item in data.get("items", [])[:n]:
            raw_price  = item.get("lprice", "0")
            title_raw  = item.get("title", "")
            title_text = BeautifulSoup(title_raw, "html.parser").get_text() if _HAS_BS4 else title_raw
            out.append(SearchResult(
                title         = title_text,
                url           = item.get("link", ""),
                snippet       = item.get("mallName", ""),
                price         = float(raw_price) if raw_price else None,
                delivery_days = 3,   # Naver doesn't expose delivery estimate in basic API
                image_url     = item.get("image", ""),
                source        = "naver",
            ))
        return out

    # ── DuckDuckGo shopping scrape ────────────────────────────────────────────

    async def _ddg_shop(self, keyword: str, n: int) -> list[SearchResult]:
        """Scrape DuckDuckGo shopping tab; fall back to web search + price regex."""
        if not _HAS_BS4:
            return []
        # DDG shopping tab
        url = "https://duckduckgo.com/"
        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as sess:
            async with sess.get(
                url,
                params={"q": keyword + " buy price", "iax": "shopping", "ia": "shopping"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("[data-testid='product-card'], .module--products__item, .shoppingResult")

        out: list[SearchResult] = []
        for card in cards[:n]:
            title_el  = card.select_one("h3, .product-title, .result__title")
            price_el  = card.select_one(".price, [data-testid='price'], .module--price")
            link_el   = card.select_one("a")
            title  = title_el.get_text(strip=True)  if title_el  else ""
            price_text = price_el.get_text(strip=True) if price_el  else ""
            href   = str(link_el.get("href", ""))   if link_el   else ""
            price  = _parse_price(price_text)

            if title:
                out.append(SearchResult(
                    title  = title,
                    url    = href,
                    price  = price,
                    source = "ddg_shop",
                ))

        # If parsing yielded nothing, fall back to web results with price regex
        if not out:
            web_results = await self._ddg_search(keyword + " 최저가 구매", n)
            for r in web_results:
                price = _parse_price(r.snippet)
                if price is not None:
                    r.price = price
            out = web_results
        return out


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_price(text: str) -> Optional[float]:
    """Extract first numeric price from a string. Returns None if not found."""
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None
