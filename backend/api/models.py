"""
api/models.py — All Pydantic request/response models for JARVIS API.
Adding a new endpoint model? Add it here, not in main.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response:        str
    simulation_mode: bool = False


class _GGAction(BaseModel):
    actionName: str  = ""
    parameters: dict = {}

class _GGIntent(BaseModel):
    extra: dict = {}

class _GGEvent(BaseModel):
    intent: _GGIntent = _GGIntent()

class _GGSession(BaseModel):
    id: str = "unknown"

class _GGDevice(BaseModel):
    id: str = "unknown"

class _GGContext(BaseModel):
    session: _GGSession = _GGSession()
    device:  _GGDevice  = _GGDevice()

class GigaGenieRequest(BaseModel):
    """
    KT GiGA Genie KSK (KT Skill Kit) webhook payload.
    Utterance extraction priority:
      1. event.intent.extra.clientMessage
      2. action.parameters.clientMessage.value
      3. legacy utterance field (backward compat / test)
    """
    version:   str        = "2.0"
    action:    _GGAction  = _GGAction()
    event:     _GGEvent   = _GGEvent()
    context:   _GGContext = _GGContext()
    # legacy / test fields
    utterance: str = ""
    userId:    str = "unknown"
    deviceId:  str = "unknown"


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


class AlertRequest(BaseModel):
    """Client-injected alert — broadcast as proactive_alert WS event."""
    message:  str
    severity: str = "HIGH"   # HIGH | NORMAL | LOW


# ── Device Context Sync ───────────────────────────────────────────────────────

class NotificationItem(BaseModel):
    """Single notification from phone."""
    app:    str = ""
    sender: str = ""
    text:   str = ""
    time:   str = ""   # e.g. "14:32"


class PhoneSyncRequest(BaseModel):
    """
    POST /sync/phone — payload from iOS Shortcut or Android Tasker.

    Minimal required fields: battery.
    All others are optional but enrich JARVIS context the more you send.

    iOS Shortcut example payload:
      {
        "battery": 45, "charging": false,
        "location_zone": "home",
        "wifi": "HomeNetwork",
        "active_app": "YouTube",
        "lat": 37.5665, "lon": 126.9780,
        "notifications": [
          {"app": "Messages", "sender": "홍길동", "text": "언제와?", "time": "14:32"}
        ]
      }
    """
    battery:       int                    = Field(default=0,  ge=0, le=100)
    charging:      bool                   = False
    location_zone: str                    = ""   # home | school | out | dorm | unknown
    wifi:          str                    = ""
    active_app:    str                    = ""
    volume:        int                    = Field(default=0,  ge=0, le=100)
    # GPS coordinates — None means not reported this cycle (not a spike/error)
    lat:           float | None           = Field(default=None, ge=-90.0,  le=90.0)
    lon:           float | None           = Field(default=None, ge=-180.0, le=180.0)
    notifications: list[NotificationItem] = []


class LaptopSyncRequest(BaseModel):
    """
    POST /sync/laptop — payload from a local script on the laptop.

    The server auto-populates CPU/RAM/battery from psutil every 5 s, but
    active_window must be pushed from the laptop (psutil can't read it).

    Python example (run on laptop):
      import subprocess, requests, psutil
      win = subprocess.check_output(['xdotool','getactivewindow','getwindowname']).decode()
      bat = psutil.sensors_battery()
      requests.post('http://158.180.78.104:8000/sync/laptop', json={
          'active_window': win.strip(),
          'battery': int(bat.percent), 'charging': bat.power_plugged,
      })
    """
    battery:       int   = 0
    charging:      bool  = False
    cpu:           float = 0.0
    ram:           float = 0.0
    disk:          float = 0.0
    active_window: str   = ""
    uptime_hours:  float = 0.0
