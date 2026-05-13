"""
client/preference_engine.py — Unified Preference & Personality Engine

Tracks user feedback on search/shopping results and adjusts ranking weights
in real time. Bio-Rhythm score controls briefing verbosity.

Storage: SQLite (same DB as jarvis.logger, separate table 'preferences').
Schema:
  preferences(domain TEXT, token TEXT, score REAL, hits INT)
  feedback_log(ts REAL, query TEXT, result_title TEXT, positive INT)

rank_results(results, bio_score):
  - Boosts results whose domain matches high-weight tokens
  - Returns list sorted by composite (preference_weight × relevance)

briefing_verbosity(bio_score) → "concise" | "normal" | "detailed"
  bio_score (= FocusScore) interpretation:
    ≤ 40  : tired / low focus → concise (1 bullet)
    41-70 : normal            → normal  (3 bullets)
    > 70  : high focus        → detailed (full analysis)
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from client.search_agent import SearchResult

log = logging.getLogger("jarvis.preference")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    domain TEXT NOT NULL,
    token  TEXT NOT NULL,
    score  REAL NOT NULL DEFAULT 1.0,
    hits   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain, token)
);
CREATE TABLE IF NOT EXISTS feedback_log (
    ts           REAL    NOT NULL,
    query        TEXT    NOT NULL,
    result_title TEXT    NOT NULL,
    positive     INTEGER NOT NULL
);
"""

_BOOST      = 0.30   # score increment on positive feedback
_DECAY      = 0.15   # score decrement on negative feedback
_SCORE_MAX  = 5.0
_SCORE_MIN  = 0.1


class PreferenceEngine:
    """
    Lightweight preference engine backed by SQLite.
    Thread-safe via WAL mode — reads from any thread, writes serialised.
    """

    def __init__(self, db_path: Path) -> None:
        self._db = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.executescript(_SCHEMA)

    # ── Public API ────────────────────────────────────────────────────────────

    def record_feedback(
        self,
        query:        str,
        result_title: str,
        result_domain: str,
        positive:     bool,
    ) -> None:
        """Record a user thumbs-up/down and update domain weight."""
        delta = _BOOST if positive else -_DECAY
        with self._conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO preferences (domain, token, score, hits) VALUES (?,?,1.0,0)",
                (result_domain, query[:40]),
            )
            con.execute(
                """UPDATE preferences
                   SET score = MAX(?, MIN(?, score + ?)), hits = hits + 1
                   WHERE domain = ? AND token = ?""",
                (_SCORE_MIN, _SCORE_MAX, delta, result_domain, query[:40]),
            )
            con.execute(
                "INSERT INTO feedback_log VALUES (?,?,?,?)",
                (time.time(), query, result_title[:80], int(positive)),
            )

    def rank_results(
        self,
        results:   list,
        bio_score: float,
        query:     str = "",
    ) -> list:
        """
        Re-rank results by preference weight × original position score.
        Also trims the result list based on briefing verbosity.
        """
        if not results:
            return results

        verbosity = self.briefing_verbosity(bio_score)
        max_items = {"concise": 2, "normal": 4, "detailed": 6}.get(verbosity, 4)

        weights = self._load_weights()
        scored: list[tuple[float, object]] = []
        for i, r in enumerate(results):
            domain = _extract_domain(getattr(r, "url", ""))
            token  = query[:40] if query else ""
            w_key  = (domain, token)
            pref_w = weights.get(w_key, weights.get((domain, ""), 1.0))
            # Position score: first result gets 1.0, each subsequent drops by 0.1
            pos_score = max(0.1, 1.0 - i * 0.10)
            scored.append((pref_w * pos_score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:max_items]]

    @staticmethod
    def briefing_verbosity(bio_score: float) -> str:
        """Map current FocusScore to a verbosity level."""
        if bio_score <= 40:
            return "concise"
        if bio_score <= 70:
            return "normal"
        return "detailed"

    @staticmethod
    def briefing_intro(bio_score: float) -> str:
        """Return a tone-matched intro line for agent result output."""
        if bio_score <= 40:
            return "요점만 — "
        if bio_score <= 70:
            return ""
        return "심화 분석 — "

    # ── Internal ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db), timeout=5.0)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _load_weights(self) -> dict[tuple[str, str], float]:
        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT domain, token, score FROM preferences"
                ).fetchall()
            return {(domain, token): score for domain, token, score in rows}
        except Exception:
            return {}


def _extract_domain(url: str) -> str:
    """Extract bare domain name from URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return url[:30]
