"""
api/models.py — All Pydantic request/response models for JARVIS API.
Adding a new endpoint model? Add it here, not in main.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response:        str
    simulation_mode: bool = False


class GigaGenieRequest(BaseModel):
    utterance: str
    userId:    str  = "unknown"
    deviceId:  str  = "unknown"
    extra:     dict = {}


class ReactorRequest(BaseModel):
    energy_saving: bool


class CalendarEvent(BaseModel):
    time:     str
    title:    str
    location: str = ""


class CalendarPayload(BaseModel):
    events: list[CalendarEvent]
    date:   str = ""


class DeployNotifyRequest(BaseModel):
    commit:  str  = ""
    message: str  = ""
    actor:   str  = "github-actions"
    failed:  bool = False


class FocusRequest(BaseModel):
    minutes: int | None = None


class RememberRequest(BaseModel):
    content: str
    key:     str | None = None
    tags:    list[str]  = []


class RecallRequest(BaseModel):
    query: str
    limit: int = 5


# ── iPhone Shortcut triggers ──────────────────────────────────────────────────

class AppOpenRequest(BaseModel):
    app_name:     str
    bundle_id:    str  = ""
    focus_active: bool = False
    timestamp:    str  = ""   # ISO 8601 from shortcut, optional


class BulkSyncRequest(BaseModel):
    """Batch of events buffered offline during dorm isolation (iPhone → HUD)."""
    events:    list[dict] = []
    device_id: str        = "iphone"
    synced_at: str        = ""


class StudyPlanRequest(BaseModel):
    subject:        str
    topic:          str
    weakness_level: int  = 3
    exam_range:     str  = ""
    target_date:    str  = ""
    notes:          str  = ""


# ── Laptop DormSyncManager HTTP-replay batch ──────────────────────────────────

class DormQueueItem(BaseModel):
    method:    str
    path:      str
    payload:   dict  = {}
    queued_at: float = 0.0


class DormBulkSyncRequest(BaseModel):
    """Batch of HTTP requests queued by DormSyncManager while in dormitory."""
    requests: list[DormQueueItem]
