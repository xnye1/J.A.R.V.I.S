"""
db/seed.py — Idempotent initial data seeder.

Inserts partner's fixed academy schedule and top-priority goals.
Safe to call on every startup — skips rows that already exist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.database import AcademySchedule, Goal, Homework, SessionLocal

log = logging.getLogger("jarvis.seed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Goals ─────────────────────────────────────────────────────────────────────

_GOALS = [
    dict(
        title="3학년 1학기 내신 전부 A",
        category="study",
        deadline="2025-07-18",   # end of 1st semester
        priority=1,
    ),
    dict(
        title="핸드폰 지옥 탈출 (도파민 디톡스)",
        category="habit",
        deadline=None,
        priority=1,
    ),
    dict(
        title="학교생활 충실",
        category="lifestyle",
        deadline=None,
        priority=1,
    ),
]

# ── Academy schedule (KST, repeating weekly) ─────────────────────────────────
# day_of_week: 0=Mon … 4=Fri, 5=Sat, 6=Sun

_SCHEDULE = [
    dict(day_of_week=4, day_name="Friday",
         start_time="19:40", end_time="21:20",
         subject="Math",    is_optional=False, notes=None),
    dict(day_of_week=5, day_name="Saturday",
         start_time="09:30", end_time="12:10",
         subject="English", is_optional=False, notes=None),
    dict(day_of_week=5, day_name="Saturday",
         start_time="13:30", end_time="16:00",
         subject="Math",    is_optional=False, notes=None),
    dict(day_of_week=6, day_name="Sunday",
         start_time="14:30", end_time="16:30",
         subject="Math",    is_optional=True,
         notes="보충 가능성 상시 체크"),
]

# ── Homework stubs ─────────────────────────────────────────────────────────────

_HOMEWORK = [
    dict(subject="Math",    title="주간 수학 복습 — 수능 기출 5제",   priority="high"),
    dict(subject="English", title="주간 영어 복습 — 단어 50개",       priority="high"),
    dict(subject="Korean",  title="국어 내신 범위 정리",               priority="normal"),
]


# ── Public entry point ────────────────────────────────────────────────────────

def seed_initial_data() -> None:
    """Called once per server startup. All operations are idempotent."""
    db = SessionLocal()
    try:
        goals_added    = _seed_goals(db)
        schedule_added = _seed_schedule(db)
        hw_added       = _seed_homework(db)
        db.commit()
        if goals_added or schedule_added or hw_added:
            log.info(
                "Seed complete — goals:%d  schedule:%d  homework:%d",
                goals_added, schedule_added, hw_added,
            )
        else:
            log.debug("Seed: all records already present, nothing inserted.")
    except Exception as exc:
        db.rollback()
        log.error("Seed failed: %s", exc)
    finally:
        db.close()


def _seed_goals(db) -> int:
    existing = {g.title for g in db.query(Goal).all()}
    added = 0
    for g in _GOALS:
        if g["title"] not in existing:
            db.add(Goal(
                title=g["title"], category=g["category"],
                deadline=g["deadline"], priority=g["priority"],
                status="active", progress=0.0,
                created_at=_utcnow(), updated_at=_utcnow(),
            ))
            added += 1
    return added


def _seed_schedule(db) -> int:
    if db.query(AcademySchedule).count() > 0:
        return 0
    for s in _SCHEDULE:
        db.add(AcademySchedule(**s))
    return len(_SCHEDULE)


def _seed_homework(db) -> int:
    if db.query(Homework).count() > 0:
        return 0
    for h in _HOMEWORK:
        db.add(Homework(
            subject=h["subject"], title=h["title"],
            status="pending", priority=h["priority"],
            created_at=_utcnow(), updated_at=_utcnow(),
        ))
    return len(_HOMEWORK)
