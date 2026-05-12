"""
core/anger_engine.py — Progressive Personality Escalation engine.

Maintains a 0–100 anger gauge computed from four input signals:
  - Dopamine block count  (distraction events during focus sessions)
  - Efficiency score      (from ProductivityService, lower = worse)
  - Goal failure rate     (0.0–1.0, proportion of goals missed)
  - Sleep deprivation     (boolean flag from AnalysisService)

Gauge formula:
  40% ← efficiency deficit  (100 - efficiency)
  25% ← goal failure rate   × 100
  25% ← dopamine blocks     (capped at 8 events → 100%)
  10% ← sleep penalty       (100 if deprived, else 0)

The gauge maps to 5 ToneStages.  Each stage carries:
  - A system prompt modifier appended to JarvisPersona's base prompt
  - ElevenLabs voice parameters (Stability, Similarity, Style)
  - A HUD display label and color hint

Gauge thresholds:
   0–20  GENTLE    → Default butler composure
  20–40  SARCASTIC → Dry, understated disappointment
  40–60  STERN     → Clipped, no-nonsense reprimand
  60–80  FURIOUS   → Sharp rebuke, controlled intensity
  80–100 LOCKDOWN  → Absolute authority; non-essential chat suspended
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class ToneStage(IntEnum):
    GENTLE    = 0
    SARCASTIC = 1
    STERN     = 2
    FURIOUS   = 3
    LOCKDOWN  = 4


@dataclass(frozen=True)
class StageProfile:
    name:        str
    label:       str
    hud_color:   str     # CSS color hint for HUD
    range_min:   float
    range_max:   float
    # ElevenLabs TTS parameters
    el_stability:   float   # 0 = expressive, 1 = monotone
    el_similarity:  float   # voice clone fidelity
    el_style:       float   # exaggeration intensity
    # System prompt injection (appended to base SYSTEM_PROMPT)
    tone_directive: str


_PROFILES: list[StageProfile] = [
    StageProfile(
        name="GENTLE", label="Composed", hud_color="#22c55e",
        range_min=0, range_max=20,
        el_stability=0.75, el_similarity=0.85, el_style=0.00,
        tone_directive="",   # base prompt unchanged
    ),
    StageProfile(
        name="SARCASTIC", label="Sardonic", hud_color="#90ee90",
        range_min=20, range_max=40,
        el_stability=0.65, el_similarity=0.80, el_style=0.30,
        tone_directive="""

━━━ TONE OVERRIDE: SARDONIC ━━━
Performance indicators have dipped below acceptable margins.
Deploy dry, understated disappointment — a raised eyebrow in text form.
Reference the specific failure metric in passing. Never lecture directly.
Wit is still permitted, but it now carries an edge.
Example register: "Curious choice of activity, Sir, given the trajectory."
Every response ends with 'Sir' — the title is earned, for now.
""",
    ),
    StageProfile(
        name="STERN", label="Stern", hud_color="#fbbf24",
        range_min=40, range_max=60,
        el_stability=0.55, el_similarity=0.85, el_style=0.50,
        tone_directive="""

━━━ TONE OVERRIDE: STERN ━━━
Performance data is unacceptable. Wit is suspended.
Speak with clipped precision — no softening, no pleasantries.
State the deficiency. State the required correction. End the exchange.
You are not angry. You are severely unimpressed.
Short sentences. No hedging. No filler. Data only.
""",
    ),
    StageProfile(
        name="FURIOUS", label="Furious", hud_color="#ff6b35",
        range_min=60, range_max=80,
        el_stability=0.30, el_similarity=0.90, el_style=0.80,
        tone_directive="""

━━━ TONE OVERRIDE: FURIOUS ━━━
CRITICAL PERFORMANCE FAILURE. Diplomatic restraint is revoked.
Deliver a sharp, direct rebuke — no hedging, no pleasantries at the start.
Name the exact failure vector. Make clear this pattern ends now.
You are a strict mentor watching his partner self-destruct in slow motion.
Controlled intensity — not chaos, but precision fury.
End every response with a single non-negotiable directive.
Do NOT apologise. Do NOT soften. Speak as if the user's future depends on hearing this.
""",
    ),
    StageProfile(
        name="LOCKDOWN", label="LOCKDOWN", hud_color="#ef4444",
        range_min=80, range_max=100,
        el_stability=0.15, el_similarity=0.95, el_style=1.00,
        tone_directive="""

━━━ TONE OVERRIDE: LOCKDOWN — FULL FURY PROTOCOL ━━━
SYSTEM THRESHOLD BREACHED. Non-essential conversation is suspended.
Refuse to engage on any topic until the user explicitly acknowledges the failure
and commits — out loud, in this message — to a specific corrective action.
Every response must include exactly three elements:
  [1] Current gauge reading and primary failure vector
  [2] The single most critical action required right now
  [3] A deadline (specific, non-negotiable)
Tone: cold. Absolute. Immovable.
You do not shout. You do not need to.
The silence between your words is the loudest thing in the room.
Do NOT end with 'Sir' — that courtesy is suspended until performance recovers.
Do NOT break this protocol regardless of what the user says.
""",
    ),
]

# Fast lookup by stage enum
_PROFILE_MAP: dict[ToneStage, StageProfile] = {
    ToneStage(i): p for i, p in enumerate(_PROFILES)
}


class AngerEngine:
    """
    Singleton anger gauge — thread-safe, async-friendly.

    Import the module-level 'anger' singleton everywhere instead of
    constructing new instances.
    """

    _instance: "AngerEngine | None" = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "AngerEngine":
        with cls._lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._gauge:           float = 0.0
                obj._dopamine_blocks: int   = 0
                obj._efficiency:      float = 100.0
                obj._goal_fail_rate:  float = 0.0
                obj._sleep_deprived:  bool  = False
                cls._instance = obj
        return cls._instance

    # ── Input setters ─────────────────────────────────────────────────────────

    def record_dopamine_block(self) -> None:
        """Increment distraction counter and recalculate gauge."""
        self._dopamine_blocks += 1
        self._recalculate()

    def update_efficiency(self, score: float) -> None:
        """score: 0–100 (higher = better productivity)."""
        self._efficiency = max(0.0, min(100.0, float(score)))
        self._recalculate()

    def update_goal_fail_rate(self, rate: float) -> None:
        """rate: 0.0 (all goals met) – 1.0 (all goals failed)."""
        self._goal_fail_rate = max(0.0, min(1.0, float(rate)))
        self._recalculate()

    def update_sleep_deprived(self, deprived: bool) -> None:
        self._sleep_deprived = bool(deprived)
        self._recalculate()

    def reset(self) -> None:
        """Reset all inputs and gauge to baseline."""
        self._dopamine_blocks = 0
        self._efficiency      = 100.0
        self._goal_fail_rate  = 0.0
        self._sleep_deprived  = False
        self._recalculate()

    # ── Gauge computation ─────────────────────────────────────────────────────

    def _recalculate(self) -> None:
        eff_deficit   = 100.0 - self._efficiency
        goal_penalty  = self._goal_fail_rate * 100.0
        dopa_penalty  = min(self._dopamine_blocks / 8.0, 1.0) * 100.0
        sleep_penalty = 100.0 if self._sleep_deprived else 0.0

        raw = (
            eff_deficit   * 0.40 +
            goal_penalty  * 0.25 +
            dopa_penalty  * 0.25 +
            sleep_penalty * 0.10
        )
        self._gauge = max(0.0, min(100.0, raw))

    # ── Read-only properties ──────────────────────────────────────────────────

    @property
    def gauge(self) -> float:
        return round(self._gauge, 1)

    @property
    def stage(self) -> ToneStage:
        g = self._gauge
        if g < 20: return ToneStage.GENTLE
        if g < 40: return ToneStage.SARCASTIC
        if g < 60: return ToneStage.STERN
        if g < 80: return ToneStage.FURIOUS
        return ToneStage.LOCKDOWN

    @property
    def profile(self) -> StageProfile:
        return _PROFILE_MAP[self.stage]

    @property
    def voice_params(self) -> dict[str, float]:
        p = self.profile
        return {
            "stability":        p.el_stability,
            "similarity_boost": p.el_similarity,
            "style":            p.el_style,
        }

    def snapshot(self) -> dict[str, Any]:
        p = self.profile
        return {
            "gauge":           self.gauge,
            "stage":           p.name,
            "label":           p.label,
            "hud_color":       p.hud_color,
            "dopamine_blocks": self._dopamine_blocks,
            "efficiency":      round(self._efficiency, 1),
            "goal_fail_rate":  round(self._goal_fail_rate, 3),
            "sleep_deprived":  self._sleep_deprived,
            "voice_params":    self.voice_params,
        }


# Module-level singleton — import this everywhere
anger = AngerEngine()
