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
