"""
JARVIS — FastAPI backend
Dual-interface: /hud (laptop) + /remote (phone) via WebSocket cross-link
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── Mode detection ────────────────────────────────────────────────────────────
_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_SIMULATION = not _KEY or not _KEY.startswith("sk-ant-") or "여기에" in _KEY
print(f"[JARVIS] {'Simulation Mode' if _SIMULATION else 'Full operational'} active.")

from core.persona import JarvisPersona, IDENTITY, SIMULATION_MODE as _SIM_PERSONA
from core.system_info import get_detailed_status, to_dict as status_to_dict
from system.monitor import ProactiveEngine, get_current_status

STATIC = Path(__file__).parent / "static"

# ── Reactor / Energy state ────────────────────────────────────────────────────
energy_saving_mode: bool = False   # True = slow poll + static HUD animations


# ── Connection Manager ────────────────────────────────────────────────────────
class ConnectionManager:
    """Tracks HUD (laptop) and Remote (phone) WebSocket connections separately."""

    def __init__(self):
        self.hud:    list[WebSocket] = []
        self.remote: list[WebSocket] = []

    def _safe_remove(self, ws: WebSocket):
        if ws in self.hud:    self.hud.remove(ws)
        if ws in self.remote: self.remote.remove(ws)

    async def _send(self, ws: WebSocket, payload: dict):
        try:
            await ws.send_json(payload)
        except Exception:
            self._safe_remove(ws)

    async def broadcast_hud(self, payload: dict):
        for ws in list(self.hud):
            await self._send(ws, payload)

    async def broadcast_remote(self, payload: dict):
        for ws in list(self.remote):
            await self._send(ws, payload)

    async def broadcast_all(self, payload: dict):
        for ws in list(self.hud + self.remote):
            await self._send(ws, payload)

    @property
    def remote_count(self) -> int:
        return len(self.remote)

    @property
    def hud_count(self) -> int:
        return len(self.hud)


# ── Globals ───────────────────────────────────────────────────────────────────
jarvis  = JarvisPersona()
manager = ConnectionManager()


async def handle_alert(alert_text: str, _status):
    response = jarvis.proactive_alert(alert_text)
    await manager.broadcast_all({"type": "proactive_alert", "message": response})
    await manager.broadcast_hud({"type": "orb_react", "intensity": 0.9, "duration": 3000})


proactive_engine = ProactiveEngine(on_alert=handle_alert)


# ── Periodic status push to HUD ──────────────────────────────────────────────
# Always ticks every 5 s so mode-changes take effect quickly;
# the broadcast itself respects the current energy-saving interval.
async def _status_broadcaster():
    import time
    last_at = 0.0
    while True:
        await asyncio.sleep(5)
        interval = 600 if energy_saving_mode else 5
        now = time.monotonic()
        if now - last_at < interval:
            continue
        last_at = now
        if manager.hud_count == 0:
            continue
        s  = get_detailed_status()
        pa = get_current_status()
        await manager.broadcast_hud({
            "type": "status",
            "data": {
                "battery_percent":  s.battery_percent,
                "battery_plugged":  s.battery_plugged,
                "cpu_percent":      s.cpu_percent,
                "memory_percent":   s.memory_percent,
                "disk_free_gb":     s.disk_free_gb,
                "disk_used_gb":     s.disk_used_gb,
                "disk_total_gb":    s.disk_total_gb,
                "disk_percent":     s.disk_percent,
                "alerts":           pa.alerts,
                "remotes_online":   manager.remote_count,
                "energy_saving":    energy_saving_mode,
            },
        })


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    t1 = asyncio.create_task(proactive_engine.start())
    t2 = asyncio.create_task(_status_broadcaster())
    print("[JARVIS] Proactive Engine + Status Broadcaster online, Sir.")
    yield
    proactive_engine.stop()
    t1.cancel(); t2.cancel()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="JARVIS Core", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    simulation_mode: bool = _SIMULATION

class GigaGenieRequest(BaseModel):
    utterance: str
    userId: str = "unknown"
    deviceId: str = "unknown"
    extra: dict = {}

class ReactorRequest(BaseModel):
    energy_saving: bool

class CalendarEvent(BaseModel):
    time: str
    title: str
    location: str = ""

class CalendarPayload(BaseModel):
    events: list[CalendarEvent]
    date: str = ""

class DeployNotifyRequest(BaseModel):
    commit:  str = ""
    message: str = ""
    actor:   str = "github-actions"
    failed:  bool = False


# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "online", "system": "JARVIS", "version": "1.0.0",
            "simulation_mode": _SIMULATION, "hud": "/hud", "remote": "/remote"}

@app.get("/persona")
async def persona_manifest():
    """Returns the live JARVIS identity config."""
    return {**IDENTITY, "simulation_mode": _SIMULATION, "core_logic": IDENTITY["core_logic"]}

@app.get("/health")
async def health():
    return {"alive": True, "simulation_mode": _SIMULATION,
            "hud_clients": manager.hud_count, "remote_clients": manager.remote_count}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty, Sir.")
    return ChatResponse(response=jarvis.chat(req.message))

@app.get("/status")
async def system_status():
    s = get_current_status()
    return {"battery_percent": s.battery_percent, "battery_plugged": s.battery_plugged,
            "cpu_percent": s.cpu_percent, "memory_percent": s.memory_percent, "alerts": s.alerts}

@app.post("/reset")
async def reset_conversation():
    jarvis.reset_conversation()
    return {"status": "conversation reset"}

@app.post("/reactor")
async def reactor_toggle(req: ReactorRequest):
    global energy_saving_mode
    energy_saving_mode = req.energy_saving
    await manager.broadcast_all({
        "type": "reactor_state",
        "energy_saving": energy_saving_mode,
    })
    mode = "ENERGY SAVING" if energy_saving_mode else "FULL POWER"
    return {"status": "ok", "mode": mode, "energy_saving": energy_saving_mode}

@app.get("/reactor")
async def reactor_status():
    return {"energy_saving": energy_saving_mode}

@app.post("/calendar")
async def calendar_push(payload: CalendarPayload):
    events = [{"time": e.time, "title": e.title, "location": e.location}
              for e in payload.events]
    await manager.broadcast_hud({
        "type": "calendar_data",
        "date": payload.date,
        "events": events,
    })
    return {"status": "ok", "event_count": len(events)}

@app.get("/telemetry")
async def telemetry_snapshot():
    s = get_detailed_status()
    return status_to_dict(s)

@app.post("/deploy-notify")
async def deploy_notify(
    req: DeployNotifyRequest,
    x_deploy_token: str = Header(default=""),
):
    """Called by GitHub Actions after a successful (or failed) deploy."""
    expected = os.getenv("DEPLOY_WEBHOOK_SECRET", "")
    if expected and x_deploy_token != expected:
        raise HTTPException(status_code=403, detail="Invalid deploy token.")

    short_sha = req.commit[:7] if req.commit else "???????"
    commit_msg = req.message[:72] if req.message else ""

    if req.failed:
        hud_msg = (
            f"⚠ DEPLOY FAILED — SHA:{short_sha} — {commit_msg} "
            f"(actor: {req.actor})"
        )
        event_type = "deploy_failed"
    else:
        hud_msg = (
            f"SYSTEM UPDATED: New version deployed via GitHub Actions "
            f"[{short_sha}] — {commit_msg}"
        )
        event_type = "system_update"

    await manager.broadcast_hud({
        "type":    event_type,
        "message": hud_msg,
        "sha":     short_sha,
        "actor":   req.actor,
        "failed":  req.failed,
    })
    await manager.broadcast_hud({
        "type": "orb_react",
        "intensity": 1.0,
        "duration":  4000 if not req.failed else 6000,
    })

    return {"status": "notified", "sha": short_sha, "failed": req.failed}

@app.post("/gigagenie")
async def gigagenie_webhook(req: GigaGenieRequest):
    """GiGA Genie bridge — receives spoken utterance, routes through JARVIS persona."""
    if not req.utterance.strip():
        raise HTTPException(status_code=400, detail="utterance is empty")

    reply = jarvis.chat(req.utterance)

    # Push to all connected HUDs so the laptop display reacts
    await manager.broadcast_hud({
        "type": "gigagenie_command",
        "utterance": req.utterance,
        "reply": reply,
        "deviceId": req.deviceId,
    })
    await manager.broadcast_hud({"type": "orb_react", "intensity": 0.8, "duration": 2000})

    # GiGA Genie expects a TTS-ready reply string
    return {"reply": reply, "simulation_mode": _SIMULATION}


# ── Page routes ───────────────────────────────────────────────────────────────
@app.get("/hud", response_class=HTMLResponse)
async def hud_page():
    return HTMLResponse((STATIC / "hud.html").read_text(encoding="utf-8"))

@app.get("/remote", response_class=HTMLResponse)
async def remote_page():
    return HTMLResponse((STATIC / "remote.html").read_text(encoding="utf-8"))


# ── WebSocket hub ─────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    device = "remote"   # default until registration

    try:
        # Expect registration as first message (5 s grace)
        try:
            reg = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
            device = reg.get("device", "remote")
        except asyncio.TimeoutError:
            pass

        if device == "hud":
            manager.hud.append(ws)
            s = get_current_status()
            await ws.send_json({
                "type": "connected", "device": "hud",
                "remotes_online": manager.remote_count,
                "simulation_mode": _SIMULATION,
                "status": {"cpu_percent": s.cpu_percent, "memory_percent": s.memory_percent,
                           "battery_percent": s.battery_percent, "battery_plugged": s.battery_plugged},
            })
        else:
            manager.remote.append(ws)
            await ws.send_json({"type": "connected", "device": "remote",
                                "simulation_mode": _SIMULATION})
            # Tell all HUDs a remote joined
            await manager.broadcast_hud({"type": "remote_connected",
                                         "count": manager.remote_count})

        # ── Message loop ──────────────────────────────────────────────────────
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                text = data.get("message", "").strip()
                if not text:
                    continue

                # Tell HUD the remote is speaking (orb reacts immediately)
                await manager.broadcast_hud({
                    "type": "remote_speaking",
                    "message": text,
                })
                await manager.broadcast_hud({"type": "orb_react", "intensity": 0.6, "duration": 500})

                # Get JARVIS response
                reply = jarvis.chat(text)

                # Send response back to the remote that asked
                await ws.send_json({"type": "chat_response", "message": reply})

                # Broadcast full exchange to all HUDs
                await manager.broadcast_hud({
                    "type": "chat_response",
                    "query": text,
                    "message": reply,
                })
                await manager.broadcast_hud({"type": "orb_react", "intensity": 1.0, "duration": 3000})

            elif msg_type == "test_signal":
                await manager.broadcast_hud({
                    "type": "test_signal",
                    "message": "Signal received from Sir's Mobile",
                })

            elif msg_type == "remote_status":
                await manager.broadcast_hud({"type": "remote_status", **data})

            elif msg_type == "reactor_toggle":
                global energy_saving_mode
                energy_saving_mode = bool(data.get("energy_saving", False))
                await manager.broadcast_all({
                    "type": "reactor_state",
                    "energy_saving": energy_saving_mode,
                })

            elif msg_type == "calendar_data":
                # Virtual calendar relay: remote → HUD
                await manager.broadcast_hud({
                    "type": "calendar_data",
                    "date":   data.get("date", ""),
                    "events": data.get("events", []),
                })

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager._safe_remove(ws)
        if device == "remote":
            await manager.broadcast_hud({"type": "remote_disconnected",
                                         "count": manager.remote_count})
