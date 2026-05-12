"""
api/routes.py — All REST endpoints as a FastAPI APIRouter.

main.py includes this router; it never defines routes directly.
This file has no WebSocket logic and no global state mutation —
it delegates to core singletons (state, dispatcher) and services.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from api.models import (
    CalendarPayload, ChatRequest, ChatResponse, DeployNotifyRequest,
    FocusRequest, GigaGenieRequest, ReactorRequest,
    RememberRequest, RecallRequest,
)
from core.anger_engine  import anger
from core.dispatcher    import dispatcher, Priority
from core.fury_tracker  import fury
from core.persona       import IDENTITY, JarvisPersona
from core.state         import state
from core.stealth       import classify_location, decide_output, FocusMode
from core.system_info   import get_detailed_status, to_dict as status_to_dict
from system.monitor     import get_current_status

router  = APIRouter()
_SIM    = not os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-ant-")
jarvis  = JarvisPersona()

# Injected by main.py after ConnectionManager is created
_manager    = None
_registry   = None

def wire(manager, registry) -> None:
    """Called by main.py during startup to inject shared objects."""
    global _manager, _registry
    _manager  = manager
    _registry = registry


# ── Info ──────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {"status": "online", "system": "JARVIS", "version": "1.0.0",
            "simulation_mode": _SIM, "hud": "/hud", "remote": "/remote"}

@router.get("/health")
async def health():
    return {"alive": True, "simulation_mode": _SIM,
            "hud_clients":    _manager.hud_count    if _manager else 0,
            "remote_clients": _manager.remote_count if _manager else 0}

@router.get("/persona")
async def persona_manifest():
    return {**IDENTITY, "simulation_mode": _SIM}

@router.get("/state")
async def global_state():
    """Full runtime state snapshot."""
    return state.snapshot()

@router.get("/status")
async def system_status():
    s = get_current_status()
    return {"battery_percent": s.battery_percent, "battery_plugged": s.battery_plugged,
            "cpu_percent": s.cpu_percent, "memory_percent": s.memory_percent,
            "alerts": s.alerts}

@router.get("/telemetry")
async def telemetry_snapshot():
    return status_to_dict(get_detailed_status())


# ── Chat ──────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty, Sir.")
    return ChatResponse(response=jarvis.chat(req.message), simulation_mode=_SIM)

@router.post("/reset")
async def reset_conversation():
    jarvis.reset_conversation()
    return {"status": "conversation reset"}


# ── Reactor ───────────────────────────────────────────────────────────────────

@router.post("/reactor")
async def reactor_toggle(req: ReactorRequest):
    state.energy_saving = req.energy_saving
    if _manager:
        await _manager.broadcast_all({"type": "reactor_state", "energy_saving": state.energy_saving})
    mode = "ENERGY SAVING" if state.energy_saving else "FULL POWER"
    return {"status": "ok", "mode": mode, "energy_saving": state.energy_saving}

@router.get("/reactor")
async def reactor_status():
    return {"energy_saving": state.energy_saving}


# ── Calendar ──────────────────────────────────────────────────────────────────

@router.post("/calendar")
async def calendar_push(payload: CalendarPayload):
    events = [{"time": e.time, "title": e.title, "location": e.location}
              for e in payload.events]
    await dispatcher.emit({"type": "calendar_data", "date": payload.date, "events": events},
                          Priority.NORMAL)
    return {"status": "ok", "event_count": len(events)}


# ── Deploy webhook ────────────────────────────────────────────────────────────

@router.post("/deploy-notify")
async def deploy_notify(req: DeployNotifyRequest, x_deploy_token: str = Header(default="")):
    expected = os.getenv("DEPLOY_WEBHOOK_SECRET", "")
    if expected and x_deploy_token != expected:
        raise HTTPException(status_code=403, detail="Invalid deploy token.")

    short_sha   = req.commit[:7] if req.commit else "???????"
    commit_msg  = req.message[:72] if req.message else ""
    event_type  = "deploy_failed" if req.failed else "system_update"
    hud_msg     = (
        f"⚠ DEPLOY FAILED — SHA:{short_sha} — {commit_msg} (actor: {req.actor})"
        if req.failed else
        f"SYSTEM UPDATED: New version deployed via GitHub Actions [{short_sha}] — {commit_msg}"
    )

    await dispatcher.emit({"type": event_type, "message": hud_msg,
                           "sha": short_sha, "actor": req.actor, "failed": req.failed},
                          Priority.CRITICAL if req.failed else Priority.HIGH)
    await dispatcher.emit({"type": "orb_react", "intensity": 1.0,
                           "duration": 6000 if req.failed else 4000}, Priority.HIGH)
    state.increment_deploys()
    return {"status": "notified", "sha": short_sha, "failed": req.failed}


# ── GiGA Genie ────────────────────────────────────────────────────────────────

@router.post("/gigagenie")
async def gigagenie_webhook(req: GigaGenieRequest):
    if not req.utterance.strip():
        raise HTTPException(status_code=400, detail="utterance is empty")
    reply = jarvis.chat(req.utterance)
    await dispatcher.emit({"type": "gigagenie_command", "utterance": req.utterance,
                           "reply": reply, "deviceId": req.deviceId}, Priority.HIGH)
    await dispatcher.emit({"type": "orb_react", "intensity": 0.8, "duration": 2000}, Priority.HIGH)
    return {"reply": reply, "simulation_mode": _SIM}


# ── Dopamine Guard ────────────────────────────────────────────────────────────

@router.post("/focus/start")
async def focus_start(req: FocusRequest):
    from services.dopamine_guard import DopamineGuard
    guard: DopamineGuard | None = _registry.get("dopamine_guard") if _registry else None
    if guard is None:
        raise HTTPException(status_code=503, detail="DopamineGuard not available.")
    session = await guard.begin_session(req.minutes)
    return {"status": "started", "minutes": session.duration_minutes}

@router.post("/focus/end")
async def focus_end():
    from services.dopamine_guard import DopamineGuard
    guard: DopamineGuard | None = _registry.get("dopamine_guard") if _registry else None
    if guard is None:
        raise HTTPException(status_code=503, detail="DopamineGuard not available.")
    return await guard.end_session() or {"status": "no active session"}

@router.get("/focus/status")
async def focus_status():
    from services.dopamine_guard import DopamineGuard
    guard: DopamineGuard | None = _registry.get("dopamine_guard") if _registry else None
    return guard.session_status() if guard else {"active": False}


# ── Memory ────────────────────────────────────────────────────────────────────

@router.post("/memory/remember")
async def memory_remember(req: RememberRequest):
    from services.memory_service import MemoryService
    svc: MemoryService | None = _registry.get("memory_service") if _registry else None
    if svc is None:
        raise HTTPException(status_code=503, detail="MemoryService not available.")
    entry = svc.remember(req.content, req.key, req.tags)
    return {"key": entry.key, "tags": entry.tags}

@router.post("/memory/search")
async def memory_search(req: RecallRequest):
    from services.memory_service import MemoryService
    svc: MemoryService | None = _registry.get("memory_service") if _registry else None
    if svc is None:
        raise HTTPException(status_code=503, detail="MemoryService not available.")
    return {"results": [e.to_dict() for e in svc.search(req.query, req.limit)]}

@router.get("/memory/stats")
async def memory_stats():
    from services.memory_service import MemoryService
    svc: MemoryService | None = _registry.get("memory_service") if _registry else None
    return svc.stats() if svc else {"error": "unavailable"}


# ── Anger Engine ──────────────────────────────────────────────────────────────

@router.get("/anger")
async def anger_status():
    """Current anger gauge snapshot — gauge, stage, voice params, inputs."""
    return anger.snapshot()

@router.post("/anger/reset")
async def anger_reset():
    """Reset anger gauge to zero (all inputs cleared)."""
    anger.reset()
    await dispatcher.emit({
        "type":  "anger_update",
        **anger.snapshot(),
    }, Priority.HIGH)
    return {"status": "reset", **anger.snapshot()}


# ── Fury Tracker ──────────────────────────────────────────────────────────────

@router.get("/fury")
async def fury_status():
    """Phone session time + current anger gauge."""
    return fury.status()


# ── Stealth / Location ────────────────────────────────────────────────────────

@router.post("/arrival")
async def gps_arrival(lat: float, lon: float, person: str = "sir",
                      airpods: bool = False, focus_mode: str = "none"):
    """
    REST alternative to the WS 'location' message.
    Phone calls this when GPS updates — triggers stealth routing + Welcome Home.
    """
    zone  = classify_location(lat, lon)
    route = decide_output(
        location=zone,
        focus_mode=FocusMode(focus_mode),
        airpods_connected=airpods,
        giga_genie_online=False,
    )
    result = {"zone": zone, "output_mode": route.mode, "reason": route.reason}

    if zone == "home" and _manager:
        from core.voice_bridge import handle_arrival
        arrival = await handle_arrival(
            lat, lon, person=person, route=route,
            broadcast_fn=_manager.broadcast_all,
            voice_params=anger.voice_params,
        )
        result.update(arrival)

    if _manager:
        await dispatcher.emit({"type": "location_update", **result}, Priority.NORMAL)

    return result


@router.get("/stealth")
async def stealth_info(lat: float = 0.0, lon: float = 0.0,
                       focus_mode: str = "none", airpods: bool = False):
    """Preview the routing decision for given context without triggering actions."""
    zone  = classify_location(lat, lon) if lat or lon else "unknown"
    route = decide_output(
        location=zone,
        focus_mode=FocusMode(focus_mode),
        airpods_connected=airpods,
        giga_genie_online=False,
    )
    return {"zone": zone, "output_mode": route.mode, "reason": route.reason}
