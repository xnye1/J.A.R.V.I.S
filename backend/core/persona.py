"""
JARVIS persona — Hybrid-Adaptive identity engine.
Conversation history is persisted in Redis when available.
"""

import os
import uuid
import google.generativeai as genai
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

_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
SIMULATION_MODE = not _GEMINI_KEY

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


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT + anger.profile.tone_directive


def _to_gemini_history(history: list[dict]) -> list[dict]:
    """Convert standard {role, content} history to Gemini {role, parts} format."""
    result = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        result.append({"role": role, "parts": [msg["content"]]})
    return result


def _make_model() -> genai.GenerativeModel:
    genai.configure(api_key=_GEMINI_KEY)
    return genai.GenerativeModel(
        "gemini-2.0-flash",
        system_instruction=_build_system_prompt(),
    )


class JarvisPersona:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self._local_history: list[dict] = []

    def _get_history(self) -> list[dict]:
        persisted = memory.load(self.session_id)
        return persisted if persisted else self._local_history

    def _put_history(self, history: list[dict]) -> None:
        self._local_history = history
        memory.save(self.session_id, history)

    def chat(self, user_message: str) -> str:
        if SIMULATION_MODE:
            stage = anger.profile
            suffix = f" [TONE STAGE: {stage.name} — Gauge {anger.gauge}%]" if stage.name != "GENTLE" else ""
            return SIMULATION_MSG + suffix

        history = self._get_history()

        try:
            model = _make_model()
            chat_session = model.start_chat(history=_to_gemini_history(history))
            response = chat_session.send_message(user_message)
            reply = response.text
        except Exception as e:
            return f"Neural link disrupted, Sir. Standing by. ({type(e).__name__})"

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        self._put_history(history)
        return reply

    def proactive_alert(self, alert_context: str) -> str:
        if SIMULATION_MODE:
            return f"[JARVIS ALERT] {alert_context}"

        try:
            model = _make_model()
            response = model.generate_content(
                f"Proactive system alert required. Context: {alert_context}. "
                f"Report in Data Protocol mode — no sentiment, facts and immediate action only. "
                f"Prefix with [JARVIS ALERT]."
            )
            return response.text
        except Exception:
            return f"[JARVIS ALERT] {alert_context}"

    def weekly_summary(self, briefing: dict) -> str:
        eff       = briefing.get("avg_efficiency", 0)
        sessions  = briefing.get("days_reviewed", 0)
        distracts = briefing.get("total_distractions", 0)
        peak      = briefing.get("fury_peak", "GENTLE")
        goals     = briefing.get("goals", [])
        topics    = briefing.get("critical_topics", [])
        dur_days  = briefing.get("duration_days", 1)

        top_goal  = goals[0]["title"] if goals else "목표 미설정"
        top_goal_p = goals[0]["progress"] if goals else 0
        weak_str  = ", ".join(f"{t['subject']} ({t['topic']})" for t in topics[:2]) or "없음"

        sim_summary = (
            f"WEEKLY DIGEST  ·  최근 {sessions}일 기록\n"
            f"{'━' * 44}\n"
            f"• 평균 효율 {eff:.0f}점, 분산 {distracts}회 — "
            f"분노 피크 {peak} 단계{'  (기숙사 {dur_days:.0f}일 격리)' if dur_days >= 1 else ''}.\n"
            f"• 주요 목표 『{top_goal}』 진행률 {top_goal_p}%  |  "
            f"집중 필요 과목: {weak_str}.\n"
            f"• {'이번 주 흐름은 회복 중입니다.' if eff >= 70 else '집중력이 기대치를 밑돌았습니다. 패턴 점검 필요.'}\n\n"
            f"다음 주 액션 플랜:\n"
            f"→ [1] 취약 과목 우선 집중 세션 (Pomodoro 4회 목표)\n"
            f"→ [2] 외출 전 15분: 주간 목표 달성률 체크 루틴 설정\n"
            f"→ [3] 분산 알림 발생 즉시 포모도로 재시작 — 허용 분산 하루 최대 3회"
        )

        if SIMULATION_MODE:
            return sim_summary

        context = (
            f"User weekly data: avg_efficiency={eff}%, distractions={distracts}, "
            f"fury_peak={peak}, goals={goals[:3]}, critical_topics={topics[:3]}, "
            f"dorm_days={dur_days:.1f}. "
            f"Write a 3-line bullet summary (Korean) + 3-item next-week action plan. "
            f"Be precise, data-driven, and end with exactly 3 '→' action items. "
            f"Prefix with 'WEEKLY DIGEST  ·  최근 {sessions}일 기록' and a separator line."
        )
        try:
            model = _make_model()
            response = model.generate_content(context)
            return response.text
        except Exception:
            return sim_summary

    def reset_conversation(self) -> None:
        self._local_history = []
        memory.clear(self.session_id)
