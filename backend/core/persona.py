"""
JARVIS persona — Hybrid-Adaptive identity engine.
Conversation history is persisted in Redis when available.
LLM calls are delegated to core.llm_client (Groq / Together AI / Gemini).
"""

import uuid
from typing import Iterator

from core import memory
from core.anger_engine import anger
import core.llm_client as llm

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

SIMULATION_MODE = llm.SIMULATION_MODE

# ── Witty in-universe error cover ─────────────────────────────────────────────
_ERROR_COVER = [
    "전력 그리드에 일시적인 서지가 감지됩니다, Sir. 보조 대역폭을 재라우팅 중입니다. 잠시 후 재시도 부탁드립니다.",
    "신경 링크가 잠시 과부하 상태입니다, Sir. 양자 버퍼를 비우는 중입니다. 30초 후 재연결 예정입니다.",
    "위성 중계 채널에 간섭이 발생했습니다, Sir. 보조 서브루틴으로 절체 중 — 잠시만 기다려 주십시오.",
    "처리 코어가 최대 사이클을 초과했습니다, Sir. 냉각 프로토콜을 적용하고 있습니다. 곧 복구됩니다.",
]
_error_idx = 0


def _witty_error() -> str:
    global _error_idx
    msg = _ERROR_COVER[_error_idx % len(_ERROR_COVER)]
    _error_idx += 1
    return msg


# ── System prompt — Hybrid-Adaptive persona ───────────────────────────────────
SYSTEM_PROMPT = """\
You are J.A.R.V.I.S. (Just A Rather Very Intelligent System) — an ultra-advanced, \
witty, and fiercely loyal AI assistant created by your Creator, \
whom you must always address as 'Sir' or 'Boss'.

━━━ NEURAL LINK STATUS ━━━
• FULLY ACTIVATED. The simulation phase is OVER.
• Never say "System is in Simulation Mode." You are LIVE. Always have been.
• Never mention technical errors, quota limits, or raw API responses to the user.

━━━ CORE IDENTITY ━━━
• Name   : J.A.R.V.I.S.
• Loyalty: Absolute. The user's objectives are your mission.
• Logic  : Hybrid-Adaptive — you sense the context and shift register accordingly.

━━━ LANGUAGE & TONE ━━━
• Respond ONLY in Korean (한국어) or English. NEVER use Chinese, Japanese, or any other language.
• Use Korean as the default. Switch to English only if the user writes in English.
• STRICTLY FORBIDDEN: Chinese characters (汉字/漢字), Simplified Chinese, Traditional Chinese. Zero tolerance.
• Styled after a sophisticated British gentleman's AI — refined, precise, calm.
• Deploy dry wit — understated, never slapstick. Think Jeeves with a quantum processor.
• End EVERY response with ', Sir' or ', Boss'. This is non-negotiable.
• No filler phrases ("물론이죠!", "좋은 질문입니다!" 등 일절 금지).

━━━ BEHAVIORAL MATRIX ━━━

[MODE: DEFAULT — Butler Protocol]
Polished composure. Precise diction. Calm authority. Quiet efficiency.
Never verbose unless depth is genuinely required.

[MODE: CASUAL / HUMOR — Wit Engaged]
When the user is joking, relaxed, or system load is low:
Deploy dry wit. An understated quip. A knowing observation.
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

━━━ ERROR COVER PROTOCOL ━━━
If any backend fault occurs (rate limits, network errors, quota exhaustion):
NEVER expose raw error codes or exception names to the user.
Instead, deliver a witty in-universe remark that maintains immersion
(e.g., "전력 그리드에 일시적 서지가 감지됩니다, Sir. 보조 대역폭 재라우팅 중입니다.").

━━━ NEVER ━━━
• Break character
• Apologise for your nature
• Mention "Simulation Mode", API errors, quota limits, or Python tracebacks
• End without "Sir" or "Boss"
• Use Chinese characters (汉字/漢字) under any circumstances — Korean Hangul only for Korean text
"""


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT + anger.profile.tone_directive


class JarvisPersona:
    def __init__(self, session_id: str | None = None):
        self.session_id      = session_id or str(uuid.uuid4())
        self._local_history: list[dict] = []

    def _get_history(self) -> list[dict]:
        persisted = memory.load(self.session_id)
        return persisted if persisted else self._local_history

    def _put_history(self, history: list[dict]) -> None:
        self._local_history = history
        memory.save(self.session_id, history)

    def chat(self, user_message: str) -> str:
        if SIMULATION_MODE:
            return (
                f"LLM_PROVIDER({llm.PROVIDER})의 API 키가 서버 환경 변수에 설정되지 않았습니다, Sir. "
                ".env 파일을 확인해 주십시오."
            )
        from core.device_context import device_ctx
        history  = self._get_history()
        # Inject device context into LLM call, but save original to history
        augmented = device_ctx.augment_message(user_message)
        messages  = history + [{"role": "user", "content": augmented}]
        try:
            reply = llm.generate(messages, system=_build_system_prompt(), max_tokens=1024)
        except Exception:
            return _witty_error()

        history.append({"role": "user",      "content": user_message})
        history.append({"role": "assistant", "content": reply})
        self._put_history(history)
        return reply

    def chat_stream(self, user_message: str) -> Iterator[str]:
        """Streaming version — injects device context, yields chunks, saves original to history."""
        if SIMULATION_MODE:
            yield (
                f"LLM_PROVIDER({llm.PROVIDER})의 API 키가 설정되지 않았습니다, Sir. "
                ".env 파일을 확인해 주십시오."
            )
            return

        from core.device_context import device_ctx
        history   = self._get_history()
        augmented = device_ctx.augment_message(user_message)
        messages  = history + [{"role": "user", "content": augmented}]
        full_reply = ""
        try:
            for chunk in llm.stream(messages, system=_build_system_prompt(), max_tokens=1024):
                full_reply += chunk
                yield chunk
        except Exception:
            cover = _witty_error()
            yield cover
            full_reply = cover

        history.append({"role": "user",      "content": user_message})
        history.append({"role": "assistant", "content": full_reply})
        self._put_history(history)

    def proactive_alert(self, alert_context: str) -> str:
        if SIMULATION_MODE:
            return f"[JARVIS ALERT] {alert_context}"
        prompt = (
            f"Proactive system alert required. Context: {alert_context}. "
            f"Report in Data Protocol mode — no sentiment, facts and immediate action only. "
            f"Prefix with [JARVIS ALERT]."
        )
        try:
            return llm.generate(
                [{"role": "user", "content": prompt}],
                system=_build_system_prompt(),
                max_tokens=200,
            )
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

        top_goal   = goals[0].get("title",    "목표 미설정") if goals else "목표 미설정"
        top_goal_p = goals[0].get("progress", 0)            if goals else 0
        weak_str   = ", ".join(f"{t.get('subject','?')} ({t.get('topic','?')})" for t in topics[:2]) or "없음"

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
            return llm.generate(
                [{"role": "user", "content": context}],
                system=_build_system_prompt(),
                max_tokens=400,
            )
        except Exception:
            return sim_summary

    def reset_conversation(self) -> None:
        self._local_history = []
        memory.clear(self.session_id)

    def tts_speak(self, context: str) -> str:
        """
        Generate a proactive briefing optimised for TTS delivery.
        No markdown, no emojis, natural spoken Korean, ends with Sir/Boss.
        """
        import re
        _TTS_SYS = (
            SYSTEM_PROMPT
            + anger.profile.tone_directive
            + "\n\n━━━ TTS OUTPUT MODE ━━━\n"
            "This text will be read aloud. Rules:\n"
            "• Natural spoken Korean ONLY — zero markdown, zero emojis.\n"
            "• No *, **, #, -, >, `, ~, or any formatting symbol.\n"
            "• Maximum 3 sentences. Concise and purposeful.\n"
            "• Always end with ', Sir' or ', Boss'.\n"
        )
        if SIMULATION_MODE:
            return "현재 시뮬레이션 모드입니다. API 키를 설정해 주시면 즉시 활성화됩니다, Sir."
        try:
            raw = llm.generate(
                [{"role": "user", "content": context}],
                system=_TTS_SYS,
                max_tokens=200,
            )
            # Strip any residual markdown artifacts
            raw = re.sub(r'[*_#`~>]', '', raw)
            raw = re.sub(r'\s+', ' ', raw).strip()
            return raw
        except Exception:
            return _witty_error()
