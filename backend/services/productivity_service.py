"""
services/productivity_service.py — Real Productivity Hub.

Data sources:
  • Homework DB table  → task board (add / complete / list)
  • Goal DB table      → goal tracker
  • anger_engine       → anger feed from real focus score

Chat command parsing (called from WS handler before LLM):
  parse_and_execute(text) → (acted: bool, summary: str)

Supported natural language patterns (Korean):
  ADD  : "수학 숙제 추가해줘 내일까지"
  DONE : "영어 과제 완료"
  LIST : "할 일 목록" / "뭐 해야 해"
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.anger_engine import anger
from services.base_service import BaseService

# ── NLP helpers ───────────────────────────────────────────────────────────────

_SUBJECT_MAP: dict[str, str] = {
    "수학": "수학", "math": "수학",
    "수1": "수학", "수2": "수학", "미적분": "수학", "확통": "수학", "기벡": "수학",
    "영어": "영어", "english": "영어", "영문법": "영어",
    "국어": "국어", "korean": "국어",
    "과학": "과학", "science": "과학",
    "물리": "물리", "physics": "물리",
    "화학": "화학", "chemistry": "화학",
    "생물": "생물", "biology": "생물",
    "지구과학": "지구과학",
    "사회": "사회",
    "역사": "역사", "한국사": "한국사", "세계사": "세계사",
    "경제": "경제", "도덕": "도덕",
    "정보": "정보", "프로그래밍": "프로그래밍",
    "코딩": "프로그래밍", "coding": "프로그래밍",
    "체육": "체육", "음악": "음악", "미술": "미술",
}

_ADD_KEYWORDS  = ["추가해줘", "추가해", "추가", "등록해줘", "저장해줘", "넣어줘"]
_DONE_KEYWORDS = ["완료", "다 했어", "다했어", "마쳤어", "끝냈어", "완성했어", "했어"]
_LIST_KEYWORDS = ["할 일 목록", "할일 목록", "숙제 목록", "뭐 해야", "할 일이 뭐야",
                  "할 일 보여줘", "할일 보여줘", "오늘 할 일"]


def _detect_subject(text: str) -> str:
    for kw, subj in _SUBJECT_MAP.items():
        if kw in text:
            return subj
    return "기타"


def _parse_deadline(text: str) -> str | None:
    today = date.today()

    if "오늘" in text:
        return today.isoformat()
    if "내일" in text:
        return (today + timedelta(days=1)).isoformat()
    if "모레" in text:
        return (today + timedelta(days=2)).isoformat()
    if "이번주" in text or "이번 주" in text:
        days = (4 - today.weekday()) % 7 or 7   # this Friday
        return (today + timedelta(days=days)).isoformat()

    day_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    for kr, num in day_map.items():
        if f"{kr}요일" in text or f"{kr}까지" in text:
            days = (num - today.weekday()) % 7 or 7
            return (today + timedelta(days=days)).isoformat()

    m = re.search(r'(\d{1,2})월\s*(\d{1,2})일', text)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            if d < today:
                d = date(today.year + 1, int(m.group(1)), int(m.group(2)))
            return d.isoformat()
        except ValueError:
            pass
    return None


def _extract_title(text: str, subject: str) -> str:
    result = text
    # Strip subject keywords
    for kw, s in _SUBJECT_MAP.items():
        if s == subject:
            result = result.replace(kw, "")
    # Strip deadline tokens
    for noise in ["까지", "오늘", "내일", "모레", "이번주", "이번 주",
                  "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]:
        result = result.replace(noise, "")
    m = re.search(r'\d{1,2}월\s*\d{1,2}일', result)
    if m:
        result = result.replace(m.group(), "")
    # Strip action keywords
    for kw in _ADD_KEYWORDS + ["야르비스", "자비스", "jarvis", "hey", "할 일", "할일", "숙제"]:
        result = re.sub(re.escape(kw), "", result, flags=re.IGNORECASE)
    result = result.strip(" ,.:!?~\t\n")
    return result or "할 일"


# ── Service ───────────────────────────────────────────────────────────────────

class ProductivityService(BaseService):

    @property
    def name(self) -> str:
        return "productivity_service"

    @property
    def display_name(self) -> str:
        return "Productivity Hub"

    async def start(self) -> None:
        self._mark_online()

    async def stop(self) -> None:
        self._mark_offline()

    # ── Real task board ───────────────────────────────────────────────────────

    def get_task_board(self) -> dict[str, Any]:
        from db.database import SessionLocal, Homework
        today_str = date.today().isoformat()
        today_dt  = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        try:
            with SessionLocal() as db:
                pending  = db.query(Homework).filter(Homework.status == "pending").count()
                done_td  = db.query(Homework).filter(
                    Homework.status == "done",
                    Homework.updated_at >= today_dt,
                ).count()
                overdue  = db.query(Homework).filter(
                    Homework.status == "pending",
                    Homework.deadline.isnot(None),
                    Homework.deadline < today_str,
                ).count()
                next_hw  = (
                    db.query(Homework)
                    .filter(Homework.status == "pending")
                    .order_by(Homework.priority.desc(), Homework.deadline)
                    .first()
                )
            total = pending + done_td
            return {
                "pending":        pending,
                "done_today":     done_td,
                "overdue":        overdue,
                "completion_pct": round(done_td / max(total, 1) * 100),
                "next_task":      f"{next_hw.subject}: {next_hw.title}" if next_hw else "없음",
            }
        except Exception:
            return {"pending": 0, "done_today": 0, "overdue": 0,
                    "completion_pct": 0, "next_task": "없음"}

    def get_goal_tracker(self) -> dict[str, Any]:
        from db.database import SessionLocal, Goal
        try:
            with SessionLocal() as db:
                active   = db.query(Goal).filter(Goal.status == "active").count()
                done     = db.query(Goal).filter(Goal.status == "completed").count()
                top_goal = (
                    db.query(Goal)
                    .filter(Goal.status == "active")
                    .order_by(Goal.priority)
                    .first()
                )
            return {
                "active_goals": active,
                "done_goals":   done,
                "top_goal":     top_goal.title if top_goal else "없음",
                "top_progress": round((top_goal.progress or 0.0) * 100) if top_goal else 0,
            }
        except Exception:
            return {"active_goals": 0, "done_goals": 0, "top_goal": "없음", "top_progress": 0}

    # ── Chat command parser ───────────────────────────────────────────────────

    def parse_and_execute(self, text: str) -> tuple[bool, str]:
        """
        Detect task-related intents and execute them.
        Returns (acted, summary) — summary is injected into LLM context if acted.
        """
        t = text.strip()

        # LIST
        if any(kw in t for kw in _LIST_KEYWORDS):
            return True, self._list_tasks()

        # ADD
        if any(kw in t for kw in _ADD_KEYWORDS):
            return self._add_task_from_text(t)

        # DONE — check done keywords followed by task description
        for kw in _DONE_KEYWORDS:
            if kw in t:
                return self._mark_done_from_text(t)

        return False, ""

    def _add_task_from_text(self, text: str) -> tuple[bool, str]:
        from db.database import SessionLocal, Homework

        subject  = _detect_subject(text)
        deadline = _parse_deadline(text)
        title    = _extract_title(text, subject)

        try:
            with SessionLocal() as db:
                hw = Homework(
                    subject=subject,
                    title=title,
                    deadline=deadline,
                    status="pending",
                    priority="normal",
                )
                db.add(hw)
                db.commit()

            dl_str = f" ({deadline}까지)" if deadline else ""
            return True, f"할 일 추가 완료 — [{subject}] {title}{dl_str}"
        except Exception as e:
            return False, f"DB 저장 실패: {e}"

    def _mark_done_from_text(self, text: str) -> tuple[bool, str]:
        from db.database import SessionLocal, Homework

        subject = _detect_subject(text)
        # Strip done keywords to get title hint
        hint = text
        for kw in _DONE_KEYWORDS:
            hint = hint.replace(kw, "")
        hint = hint.strip()

        try:
            with SessionLocal() as db:
                # Try to match by subject first, then by title keyword
                q = db.query(Homework).filter(Homework.status == "pending")
                if subject != "기타":
                    hw = q.filter(Homework.subject == subject).order_by(Homework.deadline).first()
                else:
                    hw = q.filter(Homework.title.contains(hint[:10])).first() if hint else None

                if not hw:
                    hw = q.order_by(Homework.deadline).first()  # fallback: earliest pending

                if hw:
                    hw.status     = "done"
                    hw.updated_at = datetime.now(timezone.utc)
                    db.commit()
                    return True, f"완료 처리 — [{hw.subject}] {hw.title}"
                else:
                    return True, "완료 처리할 할 일을 찾지 못했습니다. 할 일 목록을 확인해 주세요."
        except Exception as e:
            return False, f"DB 업데이트 실패: {e}"

    def _list_tasks(self) -> str:
        from db.database import SessionLocal, Homework

        try:
            with SessionLocal() as db:
                tasks = (
                    db.query(Homework)
                    .filter(Homework.status == "pending")
                    .order_by(Homework.deadline, Homework.priority.desc())
                    .limit(10)
                    .all()
                )
                if not tasks:
                    return "현재 등록된 할 일이 없습니다."
                lines = []
                for hw in tasks:
                    dl = f" ({hw.deadline}까지)" if hw.deadline else ""
                    lines.append(f"• [{hw.subject}] {hw.title}{dl}")
                return "할 일 목록:\n" + "\n".join(lines)
        except Exception:
            return "할 일 목록을 불러오는 데 실패했습니다."

    # ── Direct CRUD (called from REST routes) ─────────────────────────────────

    def add_task(self, subject: str, title: str,
                 deadline: str | None = None, priority: str = "normal") -> dict:
        from db.database import SessionLocal, Homework
        with SessionLocal() as db:
            hw = Homework(subject=subject, title=title,
                          deadline=deadline, status="pending", priority=priority)
            db.add(hw)
            db.commit()
            db.refresh(hw)
            return {"id": hw.id, "subject": hw.subject, "title": hw.title,
                    "deadline": hw.deadline, "status": hw.status}

    def complete_task(self, task_id: int) -> bool:
        from db.database import SessionLocal, Homework
        with SessionLocal() as db:
            hw = db.query(Homework).filter(Homework.id == task_id).first()
            if not hw:
                return False
            hw.status     = "done"
            hw.updated_at = datetime.now(timezone.utc)
            db.commit()
            return True

    def delete_task(self, task_id: int) -> bool:
        from db.database import SessionLocal, Homework
        with SessionLocal() as db:
            hw = db.query(Homework).filter(Homework.id == task_id).first()
            if not hw:
                return False
            db.delete(hw)
            db.commit()
            return True

    def list_tasks(self, status: str = "pending") -> list[dict]:
        from db.database import SessionLocal, Homework
        with SessionLocal() as db:
            rows = (
                db.query(Homework)
                .filter(Homework.status == status)
                .order_by(Homework.deadline, Homework.priority.desc())
                .all()
            )
            return [{"id": r.id, "subject": r.subject, "title": r.title,
                     "deadline": r.deadline, "status": r.status,
                     "priority": r.priority} for r in rows]

    # ── Broadcaster hook ──────────────────────────────────────────────────────

    def mock_report(self) -> dict:
        board = self.get_task_board()
        goals = self.get_goal_tracker()

        # Feed anger engine with real completion data
        efficiency  = float(board["completion_pct"])
        goal_fail   = round(1.0 - board["completion_pct"] / 100.0, 3)
        anger.update_efficiency(efficiency)
        anger.update_goal_fail_rate(goal_fail)

        return {
            "type": "productivity_update",
            "data": {
                "tasks_pending":    board["pending"],
                "tasks_done":       board["done_today"],
                "tasks_overdue":    board["overdue"],
                "completion_pct":   board["completion_pct"],
                "next_task":        board["next_task"],
                "active_goals":     goals["active_goals"],
                "top_goal":         goals["top_goal"],
                "top_progress":     goals["top_progress"],
            },
        }
