"""
services/intelligence_service.py — Personal Intelligence Engine skeleton.

Features (D-Day injection targets):
  - Personal search engine (indexed knowledge base)
  - Auto-summariser for articles / papers / PDFs
  - Intelligent advice generator (context-aware)
  - Interest-domain news aggregator (AI / Semiconductor / Quantum / Finance)

All methods return mock data until Phase 16 AI injection.
"""

from __future__ import annotations

import random
from typing import Any

from services.base_service import BaseService

# ── Mock pools ────────────────────────────────────────────────────────────────

_TOPICS = [
    "Quantum Error Correction", "TSMC 2nm Process", "GPT-5 Architecture",
    "KOSPI Weekly Analysis", "NVIDIA Blackwell", "Python 3.14 Features",
    "Transformer Attention Mechanisms", "DRAM Supply Dynamics",
]
_SUMMARIES = [
    "Key finding: 3-qubit entanglement achieved at room temperature — implications for NISQ.",
    "Analyst consensus: KOSPI target 2,900 by Q3 on semiconductor recovery.",
    "Abstract: Flash attention v3 reduces memory by 40% vs v2 on H100 cluster.",
    "Market note: SK Hynix HBM3E supply tightening — watch for ASP revision.",
]
_ADVICE = [
    "Cross-reference your KOSPI thesis against semiconductor sector earnings this week.",
    "Your study pace on algorithms puts you ahead of schedule. Consider adding system design.",
    "Three unread summaries match your AI interest domain. Recommend 15-min review block.",
    "Focus score dipped 12% this week. Pattern suggests evening distraction — adjust schedule.",
]


class IntelligenceService(BaseService):
    """
    Personal intelligence engine — search, summarise, advise.
    Phase 16: wire to Anthropic API + personal knowledge graph.
    """

    @property
    def name(self) -> str:
        return "intelligence_service"

    @property
    def display_name(self) -> str:
        return "Intelligence Engine"

    async def start(self) -> None:
        self._mark_online()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Mock API surface ──────────────────────────────────────────────────────

    def search(self, query: str) -> dict[str, Any]:
        """Personal knowledge-base search (mock)."""
        return {
            "query":    query,
            "hits":     random.randint(1, 12),
            "top":      random.choice(_TOPICS),
            "latency_ms": random.randint(18, 120),
        }

    def get_summary(self, source: str = "auto") -> dict[str, Any]:
        """Auto-generated summary of a document (mock)."""
        return {
            "source":  source,
            "summary": random.choice(_SUMMARIES),
            "words":   random.randint(60, 200),
            "saved_min": round(random.uniform(2, 15), 1),
        }

    def get_advice(self) -> dict[str, Any]:
        """Context-aware proactive advice (mock)."""
        return {
            "advice":    random.choice(_ADVICE),
            "confidence": random.randint(70, 97),
            "domain":    random.choice(["Finance", "Study", "Productivity", "Tech"]),
        }

    def get_news_digest(self) -> dict[str, Any]:
        """Interest-domain news aggregation (mock)."""
        return {
            "top_topic":    random.choice(_TOPICS),
            "articles_new": random.randint(0, 8),
            "reads_today":  random.randint(0, 5),
            "sentiment":    random.choice(["Bullish", "Neutral", "Bearish", "Cautious"]),
        }

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        digest = self.get_news_digest()
        adv    = self.get_advice()
        return {
            "type": "intelligence_update",
            "data": {
                "top_topic":      digest["top_topic"],
                "articles_new":   digest["articles_new"],
                "sentiment":      digest["sentiment"],
                "advice_domain":  adv["domain"],
                "advice_preview": adv["advice"][:50] + "…",
            },
        }
