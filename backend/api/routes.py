"""
api/routes.py — All REST endpoints as a FastAPI APIRouter.

main.py includes this router; it never defines routes directly.
This file has no WebSocket logic and no global state mutation —
it delegates to core singletons (state, dispatcher) and services.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("jarvis.routes")

import json

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from api.models import (
    AlertRequest,
    AppOpenRequest, BulkSyncRequest, DormBulkSyncRequest, StudyPlanRequest,
    CalendarPayload, ChatRequest, ChatResponse, DeployNotifyRequest,
    FocusRequest, GigaGenieRequest, ReactorRequest,
    RememberRequest, RecallRequest,
    PhoneSyncRequest, LaptopSyncRequest,
)
from core.anger_engine   import anger
from core.dispatcher     import dispatcher, Priority
from core.dorm_tracker   import dorm_tracker
from core.empathy_engine import empathy
from core.fury_tracker   import fury
from core.persona        import IDENTITY, JarvisPersona
from core.state          import state
from core.stealth        import (
    classify_location, current_mute_state, decide_output, FocusMode,
    is_academy_hour, is_weekend_hyperfocus,
)
from core.system_info   import get_detailed_status, to_dict as status_to_dict
from system.monitor     import get_current_status

router  = APIRouter()
jarvis: JarvisPersona  # injected by main.py via wire()

from core.persona import SIMULATION_MODE as _SIM

# Injected by main.py after ConnectionManager is created
_manager    = None
_registry   = None

def wire(manager, registry, persona: JarvisPersona | None = None) -> None:
    """Called by main.py during startup to inject shared objects."""
    global _manager, _registry, jarvis
    _manager  = manager
    _registry = registry
    jarvis    = persona if persona is not None else JarvisPersona()


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


@router.post("/tts")
async def text_to_speech(req: ChatRequest):
    """Google TTS (gTTS) — free, no API key required. Returns audio/mpeg."""
    import asyncio, io
    from fastapi.responses import Response
    from gtts import gTTS

    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty.")

    def _synth():
        buf = io.BytesIO()
        gTTS(text=text, lang="ko", slow=False).write_to_fp(buf)
        buf.seek(0)
        return buf.read()

    try:
        audio = await asyncio.to_thread(_synth)
        return Response(content=audio, media_type="audio/mpeg")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    Transcribe audio via Groq Whisper.
    Accepts: audio/webm, audio/mp4, audio/wav, audio/m4a (≤25 MB).
    Returns: {"text": "transcribed text"}
    """
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        raise HTTPException(status_code=503, detail="STT unavailable — GROQ_API_KEY not set.")
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    from openai import OpenAI
    client = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
    filename = audio.filename or "voice.webm"
    content_type = audio.content_type or "audio/webm"
    result = client.audio.transcriptions.create(
        file=(filename, audio_bytes, content_type),
        model="whisper-large-v3-turbo",
        language="ko",
    )
    return {"text": result.text.strip()}


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    SSE streaming endpoint.
    Each chunk is sent as:  data: {"chunk": "..."}\n\n
    Final frame:            data: {"done": true}\n\n

    JS example:
      const resp = await fetch('/chat/stream', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({message: 'hello'})});
      const reader = resp.body.getReader();
      // read chunks until done
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty, Sir.")

    def _sse_generator():
        for chunk in jarvis.chat_stream(req.message):
            yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
    """
    KT GiGA Genie KSK webhook — Method A (speak → JARVIS → GiGA Genie TTS).
    Accepts both real KSK payloads and legacy test payloads.
    Returns KSK-compliant {version, resultCode, output.speechText}.
    """
    # Extract utterance from KSK nested structure, fall back to legacy field
    utterance = (
        req.event.intent.extra.get("clientMessage")
        or (req.action.parameters.get("clientMessage") or {}).get("value")
        or req.utterance
    ).strip()

    if not utterance:
        return {
            "version":    "2.0",
            "resultCode": "FAIL",
            "output":     {"speechText": "죄송합니다, 말씀을 인식하지 못했습니다."},
            "directives": [],
        }

    device_id = req.context.device.id if req.context.device.id != "unknown" else req.deviceId
    session_id = req.context.session.id

    reply = jarvis.chat(utterance)

    await dispatcher.emit({
        "type":      "gigagenie_command",
        "utterance": utterance,
        "reply":     reply,
        "deviceId":  device_id,
        "sessionId": session_id,
    }, Priority.HIGH)
    await dispatcher.emit({"type": "orb_react", "intensity": 0.8, "duration": 2000}, Priority.HIGH)

    return {
        "version":    "2.0",
        "resultCode": "OK",
        "output": {
            "speechText":  reply,
            "displayText": reply,
        },
        "directives": [],
    }


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


# ── Dorm Bulk Sync (PyQt6 overlay / laptop client) ────────────────────────────

_DORM_ALLOWED_PATHS: set[str] = {
    "/calendar", "/reactor", "/memory/remember", "/focus/start", "/focus/end",
}

@router.post("/sync/dorm-bulk")
async def dorm_bulk_sync(req: DormBulkSyncRequest):
    """
    Batch HTTP-replay endpoint for the laptop DormSyncManager.

    Accepts requests queued while the client was in dormitory (offline) mode
    and replays each one via loopback.  Only whitelisted paths are accepted;
    unknown paths are rejected and counted as failed.
    """
    import httpx

    results = []
    failed  = 0
    base    = "http://127.0.0.1:8000"   # self-loopback — avoids extra network hop

    async with httpx.AsyncClient(base_url=base, timeout=10) as client:
        for item in req.requests:
            if item.path not in _DORM_ALLOWED_PATHS:
                results.append({"path": item.path, "status": "rejected", "reason": "path not whitelisted"})
                failed += 1
                continue
            try:
                resp = await client.request(item.method.upper(), item.path, json=item.payload)
                results.append({"path": item.path, "status": resp.status_code, "body": resp.json()})
            except Exception as exc:
                results.append({"path": item.path, "status": "error", "reason": str(exc)})
                failed += 1

    processed = len(req.requests) - failed
    log.info("[DormBulkSync] processed=%d failed=%d", processed, failed)
    return {"processed": processed, "failed": failed, "results": results}


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
    return {
        "zone": zone, "output_mode": route.mode, "reason": route.reason,
        "academy_hour": is_academy_hour(), "weekend_focus": is_weekend_hyperfocus(),
        "dorm": dorm_tracker.status(),
    }


# ── iPhone Shortcut Triggers ──────────────────────────────────────────────────

@router.post("/app/open")
async def shortcut_app_open(req: AppOpenRequest):
    """
    iPhone Shortcut fires this when a potentially distracting app opens.
    Logs the event; increments fury if focus/academy/weekend is active.
    """
    fury.on_message(focus_active=req.focus_active or state.dopamine_guard_active)
    payload = {
        "type":     "app_open_alert",
        "app":      req.app_name,
        "bundle":   req.bundle_id,
        "focus":    req.focus_active,
        "academy":  is_academy_hour(),
        "weekend":  is_weekend_hyperfocus(),
        "fury":     anger.gauge,
    }
    await dispatcher.emit(payload, Priority.HIGH if is_academy_hour() else Priority.NORMAL)
    return {"logged": True, "fury_gauge": anger.gauge, "fury_stage": anger.profile.name}


@router.post("/location/arrive")
async def shortcut_location_arrive(
    lat: float, lon: float,
    person: str  = "sir",
    airpods: bool = False,
    focus_mode: str = "none",
):
    """
    Shortcut-friendly GPS arrival endpoint (mirrors /arrival, simpler signature).
    Also updates DormTracker for dorm entry/exit detection.
    """
    zone  = classify_location(lat, lon)
    route = decide_output(
        location=zone,
        focus_mode=FocusMode(focus_mode),
        airpods_connected=airpods,
        giga_genie_online=False,
    )
    result: dict = {"zone": zone, "output_mode": route.mode, "reason": route.reason}

    # Dorm tracker: detect entry / exit and generate briefing
    briefing = dorm_tracker.on_location_update(zone)
    if briefing:
        await dispatcher.emit(briefing, Priority.HIGH)
        result["briefing"] = True
    elif dorm_tracker.in_dorm:
        result["standby"] = True

    if zone == "home" and _manager:
        from core.voice_bridge import handle_arrival
        arrival = await handle_arrival(
            lat, lon, person=person, route=route,
            broadcast_fn=_manager.broadcast_all,
            voice_params=None,   # uses live gauge mapping
        )
        result.update(arrival)

    if _manager:
        await dispatcher.emit({"type": "location_update", **result}, Priority.NORMAL)
        if zone == "dorm":
            await dispatcher.emit({"type": "dorm_enter", "zone": "dorm"}, Priority.HIGH)

    return result


@router.post("/focus/toggle")
async def shortcut_focus_toggle():
    """
    Toggle focus (Pomodoro) session from iPhone Shortcut.
    If active → end. If inactive → begin 25-min session.
    """
    from services.dopamine_guard import DopamineGuard
    guard: DopamineGuard | None = _registry.get("dopamine_guard") if _registry else None
    if guard is None:
        raise HTTPException(status_code=503, detail="DopamineGuard not available.")
    if guard.session_status().get("active"):
        result = await guard.end_session() or {"status": "ended"}
        return {**result, "toggled": "off"}
    session = await guard.begin_session(25)
    return {"status": "started", "minutes": session.duration_minutes, "toggled": "on"}


# ── Mute / Stealth Status ────────────────────────────────────────────────────

@router.get("/mute/status")
async def mute_status():
    """Current proactive-alert mute state based on academy schedule + quiet hours."""
    return current_mute_state()


# ── Empathy Engine ────────────────────────────────────────────────────────────

@router.get("/empathy")
async def empathy_status():
    """Current condition profile and anger sensitivity multiplier."""
    return empathy.snapshot()

@router.post("/empathy/refresh")
async def empathy_refresh():
    """Force-refresh empathy profile from DB (normally auto-refreshes hourly)."""
    empathy._last_refresh = 0.0   # invalidate cache
    return empathy.snapshot()


# ── One-Tap Weekly Summary ────────────────────────────────────────────────────

@router.post("/dorm/summary")
async def dorm_weekly_summary():
    """
    Generate a 3-line weekly digest + next-week action plan and broadcast to HUD.
    Triggered manually (One-Tap) or automatically on dorm exit.
    """
    briefing = dorm_tracker._generate_briefing(duration_hrs=0.0, exit_zone="manual")
    summary  = jarvis.weekly_summary(briefing)
    payload  = {
        "type":    "dorm_exit_briefing",
        **briefing,
        "summary": summary,
    }
    await dispatcher.emit(payload, Priority.HIGH)
    return {"status": "broadcast", "summary": summary}


# ── Client Alert Injection ───────────────────────────────────────────────────

_SEVERITY_PRIORITY = {"HIGH": Priority.HIGH, "NORMAL": Priority.NORMAL, "LOW": Priority.LOW}

@router.post("/alert")
async def inject_alert(req: AlertRequest):
    """
    Accept an alert from any client process (overlay, mobile) and broadcast
    it as a proactive_alert WS event to all HUD clients.
    Used by FocusScoreEngine (break recommendation) and PredictiveAlert (tardiness).
    """
    priority = _SEVERITY_PRIORITY.get(req.severity.upper(), Priority.HIGH)
    await dispatcher.emit({"type": "proactive_alert", "message": req.message}, priority)
    await dispatcher.emit({"type": "orb_react", "intensity": 0.7, "duration": 2000}, Priority.NORMAL)
    log.info("[Alert] Injected: %s", req.message[:80])
    return {"status": "broadcast", "severity": req.severity}


# ── Dorm & Briefing ───────────────────────────────────────────────────────────

@router.get("/dorm/status")
async def dorm_status():
    """Current dormitory isolation state."""
    return dorm_tracker.status()


@router.post("/sync/bulk")
async def sync_bulk(req: BulkSyncRequest):
    """
    Accept a batch of events buffered on the iPhone during dorm offline period.
    Re-emits each event through the dispatcher so the HUD catches up.
    """
    replayed = 0
    for event in req.events:
        if "type" in event:
            await dispatcher.emit(event, Priority.LOW)
            replayed += 1
    return {"replayed": replayed, "device_id": req.device_id}


# ── Study Plan ────────────────────────────────────────────────────────────────

@router.post("/study/plan")
async def study_plan_add(req: StudyPlanRequest):
    """Add or update a study plan entry."""
    from db.database import StudyPlan
    from db.session  import get_db
    from datetime    import datetime, timezone
    with get_db() as db:
        existing = db.query(StudyPlan).filter(
            StudyPlan.subject == req.subject,
            StudyPlan.topic   == req.topic,
        ).first()
        if existing:
            existing.weakness_level = req.weakness_level
            existing.exam_range     = req.exam_range or existing.exam_range
            existing.target_date    = req.target_date or existing.target_date
            existing.notes          = req.notes or existing.notes
            existing.updated_at     = datetime.now(timezone.utc)
            return {"action": "updated", "id": existing.id}
        row = StudyPlan(
            subject=req.subject, topic=req.topic,
            weakness_level=req.weakness_level,
            exam_range=req.exam_range, target_date=req.target_date,
            notes=req.notes, status="pending",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(row)
    return {"action": "created"}


@router.get("/study/plan")
async def study_plan_list(status: str = "pending"):
    """List study plan entries, ordered by weakness level descending."""
    from db.database import StudyPlan
    from db.session  import get_db
    with get_db() as db:
        rows = (
            db.query(StudyPlan)
            .filter(StudyPlan.status == status)
            .order_by(StudyPlan.weakness_level.desc())
            .all()
        )
    return {"plans": [
        {"id": r.id, "subject": r.subject, "topic": r.topic,
         "weakness_level": r.weakness_level, "exam_range": r.exam_range,
         "target_date": r.target_date, "status": r.status}
        for r in rows
    ]}


# ── School & Weather ──────────────────────────────────────────────────────────

@router.get("/school/meal")
async def school_meal():
    """Today's school lunch menu (NEIS API)."""
    from services.school_service import SchoolService
    svc: SchoolService | None = _registry.get("school_service") if _registry else None
    if svc is None:
        raise HTTPException(status_code=503, detail="SchoolService not available.")
    return await svc.get_today_meal()


@router.get("/weather")
async def weather():
    """Current weather at home location (Open-Meteo)."""
    from services.school_service import SchoolService
    svc: SchoolService | None = _registry.get("school_service") if _registry else None
    if svc is None:
        raise HTTPException(status_code=503, detail="SchoolService not available.")
    return await svc.get_weather()


# ── Device Context Sync ──────────────────────────────────────────────────────

@router.post("/sync/phone")
async def sync_phone(req: PhoneSyncRequest):
    """
    Receive phone state snapshot from iOS Shortcut or Android Tasker.
    Data is stored in DeviceContext and injected into every subsequent LLM call.
    """
    from core.device_context import device_ctx
    device_ctx.update_phone(req.model_dump())
    # Broadcast to HUD so it can show phone state changes
    await dispatcher.emit({
        "type":    "device_sync",
        "device":  "phone",
        "battery": req.battery,
        "charging": req.charging,
        "zone":    req.location_zone,
        "notif_count": len(req.notifications),
    }, Priority.LOW)
    return {"status": "ok", "device": "phone", "context": device_ctx.build_context_block()}


@router.post("/sync/laptop")
async def sync_laptop(req: LaptopSyncRequest):
    """
    Receive laptop state from a local script (active window title, etc.).
    CPU/RAM/battery are auto-populated by the server's psutil broadcaster,
    but active_window must come from the laptop itself.
    """
    from core.device_context import device_ctx
    device_ctx.update_laptop(req.model_dump())
    return {"status": "ok", "device": "laptop", "context": device_ctx.build_context_block()}


@router.get("/sync/context")
async def get_context():
    """View the current device context that will be injected into LLM calls."""
    from core.device_context import device_ctx
    return device_ctx.snapshot()


# ── Debug / Test ──────────────────────────────────────────────────────────────

@router.post("/debug/ghost")
async def debug_ghost(muted: bool = True, reason: str = "테스트 수업 중", icon: str = "📚"):
    """
    Dev-only: inject a stealth_update event to verify Ghost Mode on the HUD.
    muted=true → banner appears.  muted=false → banner clears.
    """
    payload = {
        "type":         "stealth_update",
        "muted":        muted,
        "academy_hour": muted,
        "quiet_hours":  False,
        "session":      "debug_test" if muted else None,
        "reason":       reason if muted else "",
        "icon":         icon if muted else "",
    }
    await dispatcher.emit(payload, Priority.NORMAL)
    return {"injected": payload}
