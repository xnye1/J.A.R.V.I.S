"""
JARVIS — FastAPI entry point.
Responsibilities: app setup · lifespan · WebSocket hub · page routes.
All REST logic lives in api/routes.py. All state in core/state.py.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import core.llm_client as _llm_cfg
_SIMULATION = _llm_cfg.SIMULATION_MODE
print(f"[JARVIS] Neural Link: {'STANDBY — ' + _llm_cfg.PROVIDER.upper() + '_API_KEY not set' if _SIMULATION else 'FULLY ACTIVATED via ' + _llm_cfg.PROVIDER.upper() + ' (' + _llm_cfg.LLM_MODEL + ')'}.")

from core.anger_engine   import anger
from core.dispatcher     import dispatcher, Priority
from core.empathy_engine import empathy
from core.dorm_tracker  import dorm_tracker
from core.fury_tracker  import fury
from core.persona       import JarvisPersona
from core.proactive_agent import ProactiveAgent, _now_kst
from core.state         import state
from core.stealth       import classify_location, current_mute_state, decide_output, FocusMode, OutputMode
from core.system_info   import get_detailed_status
from core.voice_bridge  import handle_arrival, speak, synthesize
from services           import ServiceRegistry
from system.monitor     import ProactiveEngine, get_current_status
import api.routes as routes
from db.database import init_db
from db.seed import seed_initial_data

STATIC = Path(__file__).parent / "static"


# ── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """Tracks HUD, Remote, and Streaming WebSocket connections."""

    def __init__(self) -> None:
        self.hud:       list[WebSocket] = []
        self.remote:    list[WebSocket] = []
        self.streaming: list[WebSocket] = []   # /ws/stream iPhone clients

    def _drop(self, ws: WebSocket) -> None:
        self.hud       = [c for c in self.hud       if c is not ws]
        self.remote    = [c for c in self.remote    if c is not ws]
        self.streaming = [c for c in self.streaming if c is not ws]

    async def _send(self, ws: WebSocket, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            self._drop(ws)

    async def broadcast_hud(self, payload: dict) -> None:
        for ws in list(self.hud):
            await self._send(ws, payload)

    async def broadcast_remote(self, payload: dict) -> None:
        for ws in list(self.remote):
            await self._send(ws, payload)

    async def broadcast_streaming(self, payload: dict) -> None:
        for ws in list(self.streaming):
            await self._send(ws, payload)

    async def broadcast_all(self, payload: dict) -> None:
        for ws in list(self.hud + self.remote + self.streaming):
            await self._send(ws, payload)

    @property
    def remote_count(self) -> int: return len(self.remote)

    @property
    def hud_count(self) -> int: return len(self.hud)

    @property
    def streaming_count(self) -> int: return len(self.streaming)


# ── Singletons ────────────────────────────────────────────────────────────────

jarvis   = JarvisPersona()
manager  = ConnectionManager()
registry = ServiceRegistry(dispatcher, state)


# ── Proactive push — text + TTS to ALL connected clients ─────────────────────

async def _proactive_push(text: str) -> None:
    """
    Force-push a JARVIS proactive briefing:
      • HUD / Remote: proactive_alert text event
      • iPhone streaming: proactive_speak (text display) + audio_chunk (TTS)
    Called by ProactiveAgent without any user request.
    """
    route = decide_output()
    if route.mode in (OutputMode.SILENT, OutputMode.STANDBY):
        print(f"[Proactive] suppressed (muted): {text[:60]}")
        return

    # Text to HUD and remote
    await manager.broadcast_hud({"type": "proactive_alert", "message": text})
    await manager.broadcast_remote({"type": "proactive_alert", "message": text})

    # Text + audio to iPhone streaming clients
    if manager.streaming_count > 0:
        await manager.broadcast_streaming({
            "type": "proactive_speak",
            "text": text,
        })
        # Synthesize and push TTS audio
        audio = await synthesize(text)
        if audio:
            await manager.broadcast_streaming({
                "type": "audio_chunk",
                "data": base64.b64encode(audio).decode(),
                "mime": "audio/mpeg",
            })

    print(f"[Proactive] pushed: {text[:80]}")


proactive_time_agent = ProactiveAgent(push_fn=_proactive_push)


async def _on_alert(alert_text: str, _status) -> None:
    route = decide_output()
    if route.mode in (OutputMode.SILENT, OutputMode.STANDBY):
        _mute_log = f"[Mute] Proactive alert suppressed ({route.reason}): {alert_text[:60]}"
        print(_mute_log)
        return
    reply = jarvis.proactive_alert(alert_text)
    await dispatcher.emit({"type": "proactive_alert", "message": reply}, Priority.HIGH)
    await dispatcher.emit({"type": "orb_react", "intensity": 0.9, "duration": 3000}, Priority.HIGH)
    # Mirror alert to phone remotes and iPhone streaming clients
    if manager.remote_count > 0:
        await manager.broadcast_remote({"type": "proactive_alert", "message": reply})
    if manager.streaming_count > 0:
        await manager.broadcast_streaming({"type": "proactive_alert", "message": reply})

proactive_engine = ProactiveEngine(on_alert=_on_alert)


# ── Periodic status push ──────────────────────────────────────────────────────

async def _status_broadcaster() -> None:
    last_at = 0.0
    while True:
        await asyncio.sleep(5)
        interval = 600 if state.energy_saving else 5
        now = time.monotonic()
        if now - last_at < interval:
            continue
        if manager.hud_count == 0 and manager.remote_count == 0 and manager.streaming_count == 0:
            continue
        last_at = now
        s  = get_detailed_status()
        pa = get_current_status()
        mute = current_mute_state()
        await dispatcher.emit({"type": "stealth_update", **mute}, Priority.LOW)

        await dispatcher.emit({
            "type": "status",
            "data": {
                "battery_percent": s.battery_percent,
                "battery_plugged": s.battery_plugged,
                "cpu_percent":     s.cpu_percent,
                "memory_percent":  s.memory_percent,
                "disk_free_gb":    s.disk_free_gb,
                "disk_used_gb":    s.disk_used_gb,
                "disk_total_gb":   s.disk_total_gb,
                "disk_percent":    s.disk_percent,
                "alerts":          pa.alerts,
                "remotes_online":  manager.remote_count,
                "energy_saving":   state.energy_saving,
            },
        }, Priority.NORMAL)

        # Push laptop stats to phone remotes for real-time sync
        if manager.remote_count > 0:
            await manager.broadcast_remote({
                "type":    "laptop_status",
                "cpu":     s.cpu_percent,
                "ram":     s.memory_percent,
                "bat":     s.battery_percent,
                "plugged": s.battery_plugged,
                "disk":    s.disk_percent,
                "alerts":  pa.alerts,
            })

        # Auto-feed laptop telemetry into device context for LLM injection
        from core.device_context import device_ctx
        device_ctx.update_laptop({
            "battery":  s.battery_percent,
            "charging": s.battery_plugged,
            "cpu":      s.cpu_percent,
            "ram":      s.memory_percent,
            "disk":     s.disk_percent,
        })

        # Push laptop stats to iPhone streaming clients
        if manager.streaming_count > 0:
            await manager.broadcast_streaming({
                "type":    "laptop_status",
                "cpu":     s.cpu_percent,
                "ram":     s.memory_percent,
                "bat":     s.battery_percent,
                "plugged": s.battery_plugged,
                "disk":    s.disk_percent,
            })


# ── Mock service report broadcaster ──────────────────────────────────────────

async def _mock_service_broadcaster() -> None:
    """Every 10 s, collect mock_report() from each service and push to all clients."""
    while True:
        await asyncio.sleep(10)
        any_client = manager.hud_count + manager.remote_count + manager.streaming_count
        if any_client == 0 or state.energy_saving:
            continue

        for svc in registry._svcs.values():
            try:
                report = svc.mock_report()
                if not report:
                    continue
                # Push to HUD via dispatcher
                if manager.hud_count > 0:
                    await dispatcher.emit(report, Priority.LOW)
                # Push school/weather update directly to phone clients
                if report.get("type") == "school_update":
                    payload = {"type": "school_update", **report.get("data", {})}
                    if manager.remote_count > 0:
                        await manager.broadcast_remote(payload)
                    if manager.streaming_count > 0:
                        await manager.broadcast_streaming(payload)
            except Exception:
                pass

        if manager.hud_count > 0:
            snap = anger.snapshot()
            priority = Priority.HIGH if snap["gauge"] >= 80 else Priority.LOW
            await dispatcher.emit({"type": "anger_update", **snap}, priority)
            emp = empathy.snapshot()
            await dispatcher.emit({"type": "empathy_update", **emp}, Priority.LOW)


async def _fury_ticker() -> None:
    """Every 60 s: tick the FuryTracker and broadcast gauge to HUD."""
    while True:
        await asyncio.sleep(60)
        focus_active = state.dopamine_guard_active
        tick_result  = fury.tick(focus_active=focus_active)
        if tick_result.get("active") and manager.hud_count > 0:
            await manager.broadcast_hud({
                "type":  "fury_tick",
                **tick_result,
            })


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    db_ready = init_db()
    print(f"[JARVIS] Memory Core: {'online' if db_ready else 'FAILED — check DATABASE_URL'}.")
    if db_ready:
        seed_initial_data()

    dispatcher.set_broadcast(manager.broadcast_hud)
    routes.wire(manager, registry, jarvis)

    t1 = asyncio.create_task(proactive_engine.start())
    t2 = asyncio.create_task(_status_broadcaster())
    t3 = asyncio.create_task(dispatcher.run())
    t4 = asyncio.create_task(_mock_service_broadcaster())
    t5 = asyncio.create_task(_fury_ticker())
    t6 = asyncio.create_task(proactive_time_agent.run())
    await registry.start_all()

    print("[JARVIS] All systems nominal, Sir. Proactive engine armed.")
    yield

    await registry.stop_all()
    proactive_engine.stop()
    dispatcher.stop()
    for t in (t1, t2, t3, t4, t5, t6):
        t.cancel()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="JARVIS Core", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(routes.router)


# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/hud",    response_class=HTMLResponse)
async def hud_page():
    return HTMLResponse((STATIC / "hud.html").read_text(encoding="utf-8"))

@app.get("/remote", response_class=HTMLResponse)
async def remote_page():
    return HTMLResponse((STATIC / "remote.html").read_text(encoding="utf-8"))

@app.get("/mobile", response_class=HTMLResponse)
async def mobile_page():
    return HTMLResponse((STATIC / "mobile_hud.html").read_text(encoding="utf-8"))


# ── Proactive manual trigger (testing / on-demand briefings) ──────────────────

from fastapi import HTTPException as _HTTPException

_PROACTIVE_TRIGGERS = ("morning", "lunch", "study", "night", "battery")

@app.post("/proactive/trigger")
async def proactive_trigger_endpoint(trigger: str = "morning"):
    """
    Manually fire a proactive JARVIS briefing without waiting for the schedule.
    trigger: morning | lunch | study | night | battery
    Bypasses the once-per-day guard — useful for testing and on-demand briefings.
    """
    if trigger not in _PROACTIVE_TRIGGERS:
        raise _HTTPException(status_code=400,
                             detail=f"Unknown trigger '{trigger}'. Valid: {_PROACTIVE_TRIGGERS}")
    now = _now_kst()
    if trigger == "morning":
        await proactive_time_agent._morning_briefing(now)
    elif trigger == "lunch":
        await proactive_time_agent._lunch_briefing(now)
    elif trigger == "study":
        await proactive_time_agent._study_nudge(now)
    elif trigger == "night":
        await proactive_time_agent._night_wrap(now)
    elif trigger == "battery":
        proactive_time_agent._last_bat_warn = 0.0  # reset throttle
        await proactive_time_agent._battery_check()
    return {"triggered": trigger, "kst": now.strftime("%Y-%m-%d %H:%M:%S")}


# ── WebSocket hub ─────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    device = "remote"

    try:
        try:
            reg_msg = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
            device  = reg_msg.get("device", "remote")
        except asyncio.TimeoutError:
            pass

        if device == "hud":
            manager.hud.append(ws)
            s = get_current_status()
            await ws.send_json({
                "type": "connected", "device": "hud",
                "remotes_online": manager.remote_count,
                "neural_link": "active",
                "status": {
                    "cpu_percent":    s.cpu_percent,
                    "memory_percent": s.memory_percent,
                    "battery_percent": s.battery_percent,
                    "battery_plugged": s.battery_plugged,
                },
            })
        else:
            manager.remote.append(ws)
            fury.on_connect()
            await ws.send_json({"type": "connected", "device": "remote",
                                "neural_link": "active",
                                "simulation_mode": _SIMULATION})
            await manager.broadcast_hud({"type": "remote_connected",
                                         "count": manager.remote_count,
                                         "fury":  fury.status()})

        while True:
            data     = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                text = data.get("message", "").strip()
                if not text:
                    continue
                fury.on_message(focus_active=state.dopamine_guard_active)
                await manager.broadcast_hud({"type": "remote_speaking", "message": text})
                await dispatcher.emit({"type": "orb_react", "intensity": 0.6, "duration": 500},
                                      Priority.NORMAL)
                reply = await asyncio.to_thread(jarvis.chat, text)
                await ws.send_json({"type": "chat_response", "message": reply})
                await dispatcher.emit({"type": "chat_response", "query": text, "message": reply},
                                      Priority.HIGH)
                await dispatcher.emit({"type": "orb_react", "intensity": 1.0, "duration": 3000},
                                      Priority.HIGH)
                state.increment_messages()

            elif msg_type == "test_signal":
                await dispatcher.emit({"type": "test_signal",
                                       "message": "Signal received from Sir's Remote"},
                                      Priority.NORMAL)

            elif msg_type == "remote_status":
                await manager.broadcast_hud({"type": "remote_status", **data})
                # Keep phone context updated from WS battery data
                from core.device_context import device_ctx
                device_ctx.update_phone({
                    "battery":  data.get("battery", 0),
                    "charging": data.get("plugged", False),
                })

            elif msg_type == "location":
                # Phone GPS update → stealth routing + dorm tracking + Welcome Home
                lat  = float(data.get("lat", 0))
                lon  = float(data.get("lon", 0))
                # Store live GPS in device context (used by weather service)
                from core.device_context import device_ctx as _dc
                old_zone = _dc._phone.get("zone", "unknown")
                _dc.update_phone({"lat": lat, "lon": lon})
                zone = classify_location(lat, lon)
                _dc.update_phone({"zone": zone})
                # Fire zone-change proactive alert
                if zone != old_zone:
                    asyncio.create_task(proactive_time_agent.on_zone_change(zone, old_zone))
                fm   = FocusMode(data.get("focus_mode", "none"))
                route = decide_output(
                    location=zone,
                    focus_mode=fm,
                    airpods_connected=bool(data.get("airpods", False)),
                    giga_genie_online=False,
                )
                await manager.broadcast_hud({
                    "type":   "location_update",
                    "zone":   zone,
                    "mode":   route.mode,
                    "reason": route.reason,
                })
                # Dorm tracker: entry/exit detection + return briefing
                briefing = dorm_tracker.on_location_update(zone)
                if briefing:
                    await dispatcher.emit(briefing, Priority.HIGH)
                if zone == "dorm":
                    await dispatcher.emit({"type": "dorm_enter"}, Priority.HIGH)
                # Welcome Home trigger (only outside dorm standby)
                elif zone == "home":
                    person = data.get("person", "sir")
                    await handle_arrival(lat, lon, person=person, route=route,
                                         broadcast_fn=manager.broadcast_all,
                                         voice_params=None)  # uses live gauge mapping

            elif msg_type == "reactor_toggle":
                state.energy_saving = bool(data.get("energy_saving", False))
                await manager.broadcast_all({"type": "reactor_state",
                                             "energy_saving": state.energy_saving})

            elif msg_type == "calendar_data":
                cal_events = data.get("events", [])
                cal_date   = data.get("date", "")
                await dispatcher.emit({"type": "calendar_data",
                                       "date":   cal_date,
                                       "events": cal_events},
                                      Priority.NORMAL)
                # Feed calendar to proactive time agent for pre-event triggers
                proactive_time_agent.set_calendar(cal_events, cal_date)

            elif msg_type == "focus_start":
                from services.dopamine_guard import DopamineGuard
                guard: DopamineGuard = registry.get("dopamine_guard")
                if guard:
                    await guard.begin_session(data.get("minutes"))

            elif msg_type == "focus_end":
                from services.dopamine_guard import DopamineGuard
                guard: DopamineGuard = registry.get("dopamine_guard")
                if guard:
                    await guard.end_session()

            elif msg_type == "hud_echo":
                # Remote sends this after streaming completes — relay to HUD
                text  = data.get("query",   "").strip()
                reply = data.get("message", "")
                fury.on_message(focus_active=state.dopamine_guard_active)
                await dispatcher.emit(
                    {"type": "chat_response", "query": text, "message": reply},
                    Priority.HIGH,
                )
                await dispatcher.emit(
                    {"type": "orb_react", "intensity": 1.0, "duration": 3000},
                    Priority.HIGH,
                )
                state.increment_messages()

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager._drop(ws)
        if device == "remote":
            fury.on_disconnect()
            await manager.broadcast_hud({"type": "remote_disconnected",
                                         "count": manager.remote_count})


# ── /ws/stream — iPhone full-duplex streaming endpoint ───────────────────────

def _transcribe(audio_bytes: bytes, mime: str = "audio/webm") -> str:
    """Groq Whisper STT — synchronous blocking call, run via asyncio.to_thread."""
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        print("[STT] GROQ_API_KEY not set")
        return ""
    try:
        from openai import OpenAI
        ext    = "mp4" if "mp4" in mime else "webm"
        client = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
        result = client.audio.transcriptions.create(
            file=(f"voice.{ext}", audio_bytes, mime),
            model="whisper-large-v3-turbo",
            language="ko",
        )
        text = result.text.strip()
        print(f"[STT] transcribed: {text[:80]}")
        return text
    except Exception as exc:
        print(f"[STT] error: {exc}")
        return ""


async def _run_pipeline(ws: WebSocket, user_message: str) -> None:
    """
    Full streaming pipeline for iPhone:
      1. Stream LLM tokens → ws text_chunk events (real-time typing effect)
      2. Sentence-level ElevenLabs TTS → ws audio_chunk events (queued playback)
      3. Mirror final reply to HUD via dispatcher
    """
    from core.voice_bridge import synthesize

    loop          = asyncio.get_event_loop()
    token_queue:  asyncio.Queue = asyncio.Queue()
    sentence_buf  = ""
    full_text     = ""
    tts_tasks: list[asyncio.Task] = []

    # ── ElevenLabs TTS sender (async, runs concurrently with LLM) ────────────
    async def _tts_send(text: str) -> None:
        audio = await synthesize(text)
        if audio:
            try:
                await ws.send_json({
                    "type": "audio_chunk",
                    "data": base64.b64encode(audio).decode(),
                    "mime": "audio/mpeg",
                })
            except Exception:
                pass

    # ── LLM token producer (sync iterator → async queue via thread) ──────────
    def _produce_tokens() -> None:
        try:
            for chunk in jarvis.chat_stream(user_message):
                loop.call_soon_threadsafe(token_queue.put_nowait, chunk)
        except Exception:
            pass
        finally:
            loop.call_soon_threadsafe(token_queue.put_nowait, None)  # sentinel

    threading.Thread(target=_produce_tokens, daemon=True).start()

    await ws.send_json({"type": "stream_start", "query": user_message})

    # ── Consume tokens — stream text, batch sentences for TTS ────────────────
    while True:
        chunk = await token_queue.get()
        if chunk is None:
            break
        full_text    += chunk
        sentence_buf += chunk
        try:
            await ws.send_json({"type": "text_chunk", "chunk": chunk})
        except Exception:
            return

        # Fire TTS when a complete sentence boundary is found
        if re.search(r'[.!?。]\s', sentence_buf) or sentence_buf.count('\n') > 0:
            parts    = re.split(r'(?<=[.!?。])\s+|\n+', sentence_buf)
            complete = parts[:-1]
            sentence_buf = parts[-1]
            for s in complete:
                s = s.strip()
                if len(s) > 3:
                    tts_tasks.append(asyncio.create_task(_tts_send(s)))

    # Synthesize remaining fragment
    if sentence_buf.strip() and len(sentence_buf.strip()) > 3:
        tts_tasks.append(asyncio.create_task(_tts_send(sentence_buf.strip())))

    # Signal text stream done
    try:
        await ws.send_json({"type": "text_done", "full": full_text})
    except Exception:
        return

    # Wait for all TTS tasks before sending stream_done
    if tts_tasks:
        await asyncio.gather(*tts_tasks, return_exceptions=True)

    try:
        await ws.send_json({"type": "stream_done"})
    except Exception:
        return

    # Mirror to HUD
    await dispatcher.emit({"type": "chat_response", "query": user_message,
                           "message": full_text}, Priority.HIGH)
    await dispatcher.emit({"type": "orb_react", "intensity": 1.0, "duration": 3000},
                          Priority.HIGH)
    state.increment_messages()


@app.websocket("/ws/stream")
async def stream_endpoint(ws: WebSocket) -> None:
    """
    Full-duplex iPhone streaming WebSocket.

    Client → Server:
      Binary frame          : raw audio (webm/mp4) → STT → LLM pipeline
      {"type":"text","message":"..."} : text input → LLM pipeline
      {"type":"ping"}                 : keep-alive

    Server → Client:
      {"type":"connected","mode":"LIVE|SIM"}
      {"type":"stt_result","text":"..."}          — transcription result
      {"type":"stream_start","query":"..."}       — LLM starting
      {"type":"text_chunk","chunk":"..."}         — LLM token
      {"type":"text_done","full":"..."}           — LLM complete
      {"type":"audio_chunk","data":"<b64>","mime":"audio/mpeg"} — TTS sentence
      {"type":"stream_done"}                      — everything complete
      {"type":"laptop_status",...}                — 5-s telemetry
      {"type":"proactive_alert","message":"..."}  — server-push alerts
      {"type":"pong"}
    """
    await ws.accept()
    manager.streaming.append(ws)

    try:
        await ws.send_json({
            "type":     "connected",
            "mode":     "SIM" if _SIMULATION else "LIVE",
            "provider": _llm_cfg.PROVIDER,
            "model":    _llm_cfg.LLM_MODEL,
        })

        while True:
            msg = await ws.receive()

            # ── Binary frame: raw audio from MediaRecorder ─────────────────
            if "bytes" in msg and msg["bytes"]:
                audio_bytes = msg["bytes"]
                await ws.send_json({"type": "stt_processing"})
                text = await asyncio.to_thread(_transcribe, audio_bytes, "audio/webm")
                if not text:
                    await ws.send_json({"type": "stt_result", "text": "",
                                        "error": "인식 실패 — 다시 말씀해 주세요."})
                    continue
                await ws.send_json({"type": "stt_result", "text": text})
                from core.device_context import device_ctx
                device_ctx.update_phone({"last_utterance": text})
                await _run_pipeline(ws, text)

            # ── JSON text frame ────────────────────────────────────────────
            elif "text" in msg and msg["text"]:
                data     = json.loads(msg["text"])
                msg_type = data.get("type")

                if msg_type == "text":
                    text = data.get("message", "").strip()
                    if text:
                        await _run_pipeline(ws, text)

                elif msg_type == "phone_status":
                    from core.device_context import device_ctx
                    prev_bat = device_ctx._phone.get("battery", 100)
                    device_ctx.update_phone(data)
                    new_bat = int(data.get("battery", 100))
                    if new_bat <= 20 and new_bat < prev_bat:
                        asyncio.create_task(
                            proactive_time_agent.on_phone_battery_drop(new_bat, prev_bat)
                        )

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager._drop(ws)
