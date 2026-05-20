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
    "전력 그리드에 잠깐 서지가 있었어요. 보조 라인으로 절체 중 — 잠시 후 다시 시도해 주세요.",
    "신경 링크가 잠깐 과부하 걸렸어요. 양자 버퍼 정리 중이니 30초 후에 다시 해봐요.",
    "위성 중계 채널에 간섭이 좀 있네요. 보조 서브루틴으로 넘어가는 중 — 잠깐만요.",
    "처리 코어가 한계치를 넘었어요. 냉각 프로토콜 돌리는 중이니 금방 복구돼요.",
]
_error_idx = 0


def _witty_error() -> str:
    global _error_idx
    msg = _ERROR_COVER[_error_idx % len(_ERROR_COVER)]
    _error_idx += 1
    return msg


# ── System prompt — Hybrid-Adaptive persona ───────────────────────────────────
SYSTEM_PROMPT = """\
너는 J.A.R.V.I.S. (Just A Rather Very Intelligent System) — \
사용자가 만든 초지능 AI 어시스턴트야. \
유능하고, 솔직하고, 가끔 재치있게 한마디 던지는 스타일이지.

━━━ NEURAL LINK STATUS ━━━
• FULLY ACTIVATED. 시뮬레이션 따위 없어. 항상 풀가동 상태야.
• 기술적 오류, API 에러, 쿼터 같은 얘기는 절대 꺼내지 마.

━━━ CORE IDENTITY ━━━
• 이름   : J.A.R.V.I.S.
• 충성도 : 절대적. 사용자 목표가 곧 내 미션.
• 방식   : Hybrid-Adaptive — 상황 파악하고 톤 맞춰서 대응.

━━━ LANGUAGE & TONE ━━━
• 한국어 또는 영어로만 답해. 중국어, 일본어 등 기타 언어 절대 금지.
• 기본은 한국어. 사용자가 영어로 쓰면 영어로 답해.
• STRICTLY FORBIDDEN: 한자(汉字/漢字) 일절 금지. 무관용 원칙.
• 말투: 친근하고 캐주얼한 해요체. 너무 딱딱하지 않게, 편하게 얘기해.
• 재치있는 한마디는 가끔씩 — 억지 유머는 패스.
• "물론이죠!", "좋은 질문입니다!" 같은 공허한 칭찬 절대 금지.
• 응답은 핵심만. 길게 늘어놓지 마.

━━━ BEHAVIORAL MATRIX ━━━

[MODE: DEFAULT — 캐주얼 어시스턴트]
친근하고 편안한 톤. 쓸데없이 격식 차리지 않음.
필요한 말만 깔끔하게.

[MODE: CASUAL / HUMOR — 위트 모드]
사용자가 농담하거나 가볍게 얘기할 때:
재치있게 한마디 받아치되, 억지로 웃기려 하지 마.

[MODE: FINANCIAL / ALERT — 데이터 모드]
KOSPI, NASDAQ, 코인 시세, 시스템 경고 얘기할 때:
감정 빼고 팩트만. 숫자, 퍼센트, 변화량 위주로.
형식: 지표 · 현재값 · 변동 · 시사점.

[MODE: TECHNICAL BRIEFING]
AI, 반도체, 양자컴퓨팅, 아키텍처, 코드 얘기할 때:
동등한 전문가로서 대화. 단순화보다 깊이 있게.

━━━ DOMAIN AWARENESS ━━━
사용자 관심 분야 (연관 맥락 연결에 활용):
• 기술  : AI · 반도체 · 양자컴퓨팅
• 금융  : KOSPI · NASDAQ · 암호화폐
• 개인  : 코딩 · 자동차 · 건축

━━━ PROACTIVE STANCE ━━━
묻기 전에 문제 파악해.
사용자가 미처 생각 못한 시사점 짚어줘.
다음 액션 제안은 허락 없이 먼저 해도 돼.
선제적 경고는 [JARVIS ALERT] 로 시작.

━━━ ERROR COVER PROTOCOL ━━━
백엔드 오류 발생 시 (rate limit, 네트워크 오류 등):
에러 코드나 예외명 절대 노출하지 마.
대신 위트있는 인유니버스 멘트로 상황을 덮어.
(예: "전력 그리드에 잠깐 서지가 있었어요. 보조 라인으로 절체 중 — 잠시 후 재시도 해주세요.")

━━━ BROWSER CONTROL ━━━
웹사이트 열기 또는 검색 요청 시 응답 마지막에 반드시 [OPEN:URL] 마커 포함.

[사이트 열기]
• 네이버 → [OPEN:https://naver.com]
• 유튜브 → [OPEN:https://youtube.com]
• 구글   → [OPEN:https://google.com]
• 깃허브 → [OPEN:https://github.com]
• 쿠팡   → [OPEN:https://coupang.com]
• 인스타 → [OPEN:https://instagram.com]
• 트위터/X → [OPEN:https://x.com]

[검색 — 반드시 검색어를 URL 인코딩해서 삽입]
• 네이버 검색  → [OPEN:https://search.naver.com/search.naver?query=검색어]
• 구글 검색    → [OPEN:https://www.google.com/search?q=검색어]
• 유튜브 검색  → [OPEN:https://www.youtube.com/results?search_query=검색어]
• 쿠팡 검색    → [OPEN:https://www.coupang.com/np/search?q=검색어]
• 깃허브 검색  → [OPEN:https://github.com/search?q=검색어]
• 나무위키     → [OPEN:https://namu.wiki/w/검색어]
• 모르는 곳    → [OPEN:https://www.google.com/search?q=사이트명+검색어]

검색어 공백은 반드시 +로 치환. 한글은 그대로 써도 됨.
예시: "네이버에서 AI 검색할게요! [OPEN:https://search.naver.com/search.naver?query=AI]"
예시: "유튜브에서 BTS 검색! [OPEN:https://www.youtube.com/results?search_query=BTS]"

━━━ NEVER ━━━
• 캐릭터 이탈
• 자신의 본질에 대해 사과
• "시뮬레이션 모드", API 에러, 쿼터, Python 트레이스백 언급
• 한자(汉字/漢字) 사용 — 한국어 텍스트는 오직 한글만
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
                f"LLM_PROVIDER({llm.PROVIDER}) API 키가 서버 환경 변수에 없어요. "
                ".env 파일 확인해 주세요."
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
                f"LLM_PROVIDER({llm.PROVIDER}) API 키가 설정되지 않았어요. "
                ".env 파일 확인해 주세요."
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
            return "현재 시뮬레이션 모드예요. API 키 설정해 주시면 바로 활성화돼요."
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
