"""
db/seed.py — Idempotent initial data seeder.

Inserts partner's fixed academy schedule and top-priority goals.
Safe to call on every startup — skips rows that already exist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.database import AcademySchedule, Goal, Homework, SessionLocal, StudyPlan

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
        sp_added       = _seed_study_plans(db)
        db.commit()
        if any([goals_added, schedule_added, hw_added, sp_added]):
            log.info(
                "Seed complete — goals:%d  schedule:%d  homework:%d  study_plans:%d",
                goals_added, schedule_added, hw_added, sp_added,
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


_STUDY_PLANS = [
    # Math weaknesses
    dict(subject="Math",    topic="수열의 극한",       weakness_level=4,
         exam_range="1학기 중간 — 수열과 급수 전범위", target_date="2025-04-25"),
    dict(subject="Math",    topic="적분법 (치환적분)",  weakness_level=5,
         exam_range="1학기 기말 — 미적분 전범위",       target_date="2025-07-10"),
    dict(subject="Math",    topic="수열 점화식",         weakness_level=3,
         exam_range="1학기 중간",                        target_date="2025-04-25"),
    # English weaknesses
    dict(subject="English", topic="빈칸 추론 (고난도)", weakness_level=5,
         exam_range="전 범위 (수능형)",                  target_date=None),
    dict(subject="English", topic="어법·어휘",           weakness_level=3,
         exam_range="1학기 내신 전범위",                  target_date="2025-07-10"),
    # Korean
    dict(subject="Korean",  topic="비문학 독해 (과학·기술 지문)", weakness_level=4,
         exam_range="1학기 기말",                        target_date="2025-07-10"),
    dict(subject="Korean",  topic="문학 — 현대시 분석",  weakness_level=3,
         exam_range="1학기 기말",                        target_date="2025-07-10"),
]


def _seed_study_plans(db) -> int:
    if db.query(StudyPlan).count() > 0:
        return 0
    for sp in _STUDY_PLANS:
        db.add(StudyPlan(
            subject=sp["subject"], topic=sp["topic"],
            weakness_level=sp["weakness_level"],
            exam_range=sp["exam_range"], target_date=sp["target_date"],
            status="pending", created_at=_utcnow(), updated_at=_utcnow(),
        ))
    return len(_STUDY_PLANS)
