"""
db/schema.py — Data structure definitions for future database persistence.

Currently JARVIS runs entirely in-memory. These TypedDicts mirror the
live data shapes so a future Postgres / SQLite migration is a drop-in
(add SQLAlchemy/Tortoise models that match these fields).

No DB connections are opened here — import freely with zero side-effects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, TypedDict


# ── Chat history ──────────────────────────────────────────────────────────────

class ChatMessageRow(TypedDict):
    id:           int           # PK, auto-increment
    session_id:   str           # UUID grouping a conversation
    role:         str           # "user" | "assistant" | "system"
    content:      str
    created_at:   datetime
    tokens_used:  Optional[int]


# ── Long-term memory ──────────────────────────────────────────────────────────

class MemoryEntryRow(TypedDict):
    id:          int
    key:         str            # human-readable slug, unique
    content:     str
    tags:        str            # JSON array stored as text
    embedding:   Optional[str]  # future: pgvector / sqlite-vec float[]
    created_at:  datetime
    accessed_at: datetime
    access_count: int


# ── Daily performance snapshot ────────────────────────────────────────────────

class DailySessionRow(TypedDict):
    id:               int
    date:             str           # ISO 8601 "YYYY-MM-DD"
    study_hours:      float
    focus_sessions:   int
    distraction_hits: int           # dopamine blocks fired
    messages_sent:    int
    anger_peak:       float         # 0–100 max gauge reached
    anger_stage_peak: str           # GENTLE … LOCKDOWN
    sleep_deprived:   bool
    efficiency_score: float         # 0–100 from ProductivityService
    goal_fail_rate:   float         # 0.0–1.0
    created_at:       datetime


# ── Goal tracking ─────────────────────────────────────────────────────────────

class GoalRow(TypedDict):
    id:          int
    title:       str
    category:    str            # "study" | "health" | "project" | "habit"
    deadline:    Optional[str]  # ISO date
    status:      str            # "active" | "completed" | "failed" | "deferred"
    progress:    float          # 0.0–1.0
    created_at:  datetime
    updated_at:  datetime


# ── Deployment log ────────────────────────────────────────────────────────────

class DeployRow(TypedDict):
    id:         int
    sha:        str             # 7-char short SHA
    actor:      str             # GitHub username
    message:    str             # commit message (first 72 chars)
    failed:     bool
    deployed_at: datetime


# ── Anger/Fury history ────────────────────────────────────────────────────────

class AngerSnapshotRow(TypedDict):
    id:               int
    gauge:            float
    stage:            str
    dopamine_blocks:  int
    efficiency:       float
    goal_fail_rate:   float
    sleep_deprived:   bool
    recorded_at:      datetime
