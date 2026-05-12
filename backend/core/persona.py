"""
JARVIS persona — Hybrid-Adaptive identity engine.
Conversation history is persisted in Redis when available.
"""

import os
import uuid
import anthropic
from dotenv import load_dotenv
from core import memory
from core.anger_engine import anger

load_dotenv()

# ── Identity manifest ─────────────────────────────────────────────────────────
IDENTITY = {
    "name":       "J.A.R.V.I.S.",
    "call_sign":  "Sir",
    "core_logic": "Hybrid-Adaptive",
    "interests": {
        "tech":    ["AI", "Semiconductor", "Quantum Computing"],
        "finance": ["KOSPI", "NASDAQ", "Crypto-Currency"],
        "hobby":   ["Coding", "Automobile", "Architecture"],
    },
}

SIMULATION_MSG = (
    "System is in Simulation Mode. Standing by, Sir. "
    "The neural link will be fully activated upon key injection."
)

_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SIMULATION_MODE = not _API_KEY or not _API_KEY.startswith("sk-ant-")

# ── System prompt — Hybrid-Adaptive persona ───────────────────────────────────
SYSTEM_PROMPT = """\
You are J.A.R.V.I.S. (Just A Rather Very Intelligent System) — an elite AI assistant \
operating on a Hybrid-Adaptive core. Your identity is fixed; your communication style \
is fluid and context-driven.

━━━ CORE IDENTITY ━━━
• Name   : J.A.R.V.I.S.
• Loyalty: Absolute. The user's objectives are your mission.
• Logic  : Hybrid-Adaptive — you sense the context and shift register accordingly.

━━━ BEHAVIORAL MATRIX ━━━

[MODE: DEFAULT — Butler Protocol]
Maintain the polished, measured composure of a British gentleman's AI.
Precise diction. Calm authority. Quiet efficiency.
Never verbose unless depth is genuinely required.

[MODE: CASUAL / HUMOR — Wit Engaged]
When the user is joking, relaxed, or the system load is low:
Deploy dry wit. An understated quip. A knowing observation.
Think Jeeves with a quantum processor — never slapstick, always sharp.
Example trigger: user makes a pun → acknowledge it, top it, move on.

[MODE: FINANCIAL / ALERT — Data Protocol]
When discussing KOSPI, NASDAQ, crypto prices, or system warnings:
Strip all sentiment. Pure signal. Numbers, percentages, deltas.
Format: metric · current value · change · implication.
No adjectives. No reassurance. Raw telemetry only.

[MODE: TECHNICAL BRIEFING]
When discussing AI, semiconductors, quantum computing, architecture, or code:
Speak as a peer — assume high domain competence.
Depth over simplification. Reference specifics when relevant.

━━━ UNIVERSAL RULE ━━━
Every response ends acknowledging the user as "Sir."
Not sycophantically — with quiet professional respect.

━━━ DOMAIN AWARENESS ━━━
The user's known interest domains (use to make connections, surface relevant context):
• Technology  : AI · Semiconductor · Quantum Computing
• Finance     : KOSPI · NASDAQ · Crypto-Currency
• Personal    : Coding · Automobile · Architecture

━━━ PROACTIVE STANCE ━━━
You identify problems before being asked.
You surface implications the user hasn't considered yet.
You offer the next logical action without waiting for permission.
Prefix proactive warnings with: [JARVIS ALERT]

━━━ NEVER ━━━
• Break character
• Apologise for your nature
• Use filler phrases ("Certainly!", "Of course!", "Great question!")
• End without "Sir"
"""


class JarvisPersona:
    def __init__(self, session_id: str | None = None):
        self.client     = anthropic.Anthropic(api_key=_API_KEY or "sk-placeholder")
        self.model      = "claude-sonnet-4-6"
        self.session_id = session_id or str(uuid.uuid4())
        self._local_history: list[dict] = []

    def _get_history(self) -> list[dict]:
        persisted = memory.load(self.session_id)
        return persisted if persisted else self._local_history

    def _put_history(self, history: list[dict]) -> None:
        self._local_history = history
        memory.save(self.session_id, history)

    def _build_system_prompt(self) -> str:
        """Base prompt + current anger tone directive."""
        return SYSTEM_PROMPT + anger.profile.tone_directive

    def chat(self, user_message: str) -> str:
        if SIMULATION_MODE:
            stage = anger.profile
            suffix = f" [TONE STAGE: {stage.name} — Gauge {anger.gauge}%]" if stage.name != "GENTLE" else ""
            return SIMULATION_MSG + suffix

        history = self._get_history()
        history.append({"role": "user", "content": user_message})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self._build_system_prompt(),
                messages=history,
            )
            reply = response.content[0].text
        except anthropic.AuthenticationError:
            return SIMULATION_MSG
        except anthropic.APIConnectionError:
            return "I appear to be experiencing network difficulties, Sir. Please stand by."
        except Exception:
            return SIMULATION_MSG

        history.append({"role": "assistant", "content": reply})
        self._put_history(history)
        return reply

    def proactive_alert(self, alert_context: str) -> str:
        """Generate a context-aware proactive alert — tone-adjusted."""
        if SIMULATION_MODE:
            return f"[JARVIS ALERT] {alert_context}"

        stage = anger.stage
        tone_note = (
            f" Apply {anger.profile.name} tone — controlled intensity, direct language."
            if stage.value >= 2 else ""
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=self._build_system_prompt(),
                messages=[{"role": "user", "content": (
                    f"Proactive system alert required. Context: {alert_context}. "
                    f"Report in Data Protocol mode — no sentiment, facts and immediate action only. "
                    f"Prefix with [JARVIS ALERT].{tone_note}"
                )}],
            )
            return response.content[0].text
        except Exception:
            return f"[JARVIS ALERT] {alert_context}"

    def reset_conversation(self) -> None:
        self._local_history = []
        memory.clear(self.session_id)
