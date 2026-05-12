"""
db/database.py — SQLAlchemy ORM models + engine factory.

Default backend: SQLite (zero-config, lives in /app/jarvis.db inside Docker).
To migrate to PostgreSQL or Oracle, change DATABASE_URL in .env — nothing else.

  SQLite   : sqlite:///./jarvis.db           (default)
  Postgres : postgresql://user:pass@host/db
  Oracle   : oracle+oracledb://user:pass@host:port/service
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    create_engine, text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

log = logging.getLogger("jarvis.db")

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./jarvis.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ── Tables ────────────────────────────────────────────────────────────────────

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(36), index=True, default="default")
    role       = Column(String(16))           # user | assistant | system
    content    = Column(Text)
    tokens_used = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id           = Column(Integer, primary_key=True, index=True)
    key          = Column(String(128), unique=True, index=True)
    content      = Column(Text)
    tags         = Column(Text, default="[]")  # JSON array as text
    created_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    accessed_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    access_count = Column(Integer, default=0)


class LocationLog(Base):
    __tablename__ = "location_logs"

    id          = Column(Integer, primary_key=True)
    zone        = Column(String(32))           # home | academy | library | unknown
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    output_mode = Column(String(32))           # voice_phone | voice_genie | quiet | silent
    person      = Column(String(32), default="sir")
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FuryHistory(Base):
    __tablename__ = "fury_history"

    id               = Column(Integer, primary_key=True)
    gauge            = Column(Float)
    stage            = Column(String(16))
    dopamine_blocks  = Column(Integer, default=0)
    efficiency       = Column(Float, default=100.0)
    goal_fail_rate   = Column(Float, default=0.0)
    sleep_deprived   = Column(Boolean, default=False)
    recorded_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Homework(Base):
    __tablename__ = "homework"

    id         = Column(Integer, primary_key=True)
    subject    = Column(String(64))
    title      = Column(String(256))
    deadline   = Column(String(10), nullable=True)  # YYYY-MM-DD
    status     = Column(String(16), default="pending")  # pending | done | late
    priority   = Column(String(8),  default="normal")   # low | normal | high
    notes      = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DailySession(Base):
    __tablename__ = "daily_sessions"

    id               = Column(Integer, primary_key=True)
    date             = Column(String(10), unique=True, index=True)  # YYYY-MM-DD
    study_hours      = Column(Float,   default=0.0)
    focus_sessions   = Column(Integer, default=0)
    distraction_hits = Column(Integer, default=0)
    messages_sent    = Column(Integer, default=0)
    anger_peak       = Column(Float,   default=0.0)
    anger_stage_peak = Column(String(16), default="GENTLE")
    sleep_deprived   = Column(Boolean, default=False)
    efficiency_score = Column(Float,   default=100.0)
    goal_fail_rate   = Column(Float,   default=0.0)
    created_at       = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DeployLog(Base):
    __tablename__ = "deploy_logs"

    id          = Column(Integer, primary_key=True)
    sha         = Column(String(7))
    actor       = Column(String(64))
    message     = Column(String(256))
    failed      = Column(Boolean, default=False)
    deployed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Goal(Base):
    __tablename__ = "goals"

    id         = Column(Integer, primary_key=True)
    title      = Column(String(256), unique=True, index=True)
    category   = Column(String(32))              # study | habit | lifestyle | health
    deadline   = Column(String(10), nullable=True)  # YYYY-MM-DD
    status     = Column(String(16), default="active")  # active | completed | failed | deferred
    priority   = Column(Integer,   default=2)    # 1=highest, 2=normal, 3=low
    progress   = Column(Float,     default=0.0)  # 0.0–1.0
    created_at = Column(DateTime,  default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime,  default=lambda: datetime.now(timezone.utc))


class AcademySchedule(Base):
    __tablename__ = "academy_schedules"

    id          = Column(Integer, primary_key=True)
    day_of_week = Column(Integer)       # 0=Mon … 6=Sun
    day_name    = Column(String(16))    # human label
    start_time  = Column(String(5))     # "HH:MM" KST
    end_time    = Column(String(5))     # "HH:MM" KST
    subject     = Column(String(32))    # Math | English | …
    is_optional = Column(Boolean, default=False)
    notes       = Column(Text, nullable=True)


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id              = Column(Integer, primary_key=True)
    subject         = Column(String(32))           # Math | English | Korean | …
    topic           = Column(String(256))          # chapter / concept name
    weakness_level  = Column(Integer, default=3)   # 1=strong … 5=critical weakness
    exam_range      = Column(Text, nullable=True)  # exam scope description
    target_date     = Column(String(10), nullable=True)  # YYYY-MM-DD
    status          = Column(String(16), default="pending")  # pending|in_progress|mastered
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DormLog(Base):
    """Records each dormitory stay (entry + exit) for the weekly briefing."""
    __tablename__ = "dorm_logs"

    id            = Column(Integer, primary_key=True)
    entered_at    = Column(DateTime)
    exited_at     = Column(DateTime, nullable=True)
    duration_hrs  = Column(Float, nullable=True)
    briefing_sent = Column(Boolean, default=False)


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db() -> bool:
    """Create all tables. Safe to call multiple times (CREATE IF NOT EXISTS)."""
    try:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("Database ready — %s", DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL)
        return True
    except Exception as exc:
        log.error("Database init failed: %s", exc)
        return False
