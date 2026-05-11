"""
JARVIS — FastAPI backend
Dual-interface: /hud (laptop) + /remote (phone) via WebSocket cross-link
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── Mode detection ────────────────────────────────────────────────────────────
_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_SIMULATION = not _KEY or not _KEY.startswith("sk-ant-") or "여기에" in _KEY
print(f"[JARVIS] {'Simulation Mode' if _SIMULATION else 'Full operational'} active.")

from core.persona import JarvisPersona
from system.monitor import ProactiveEngine, get_current_status

STATIC = Path(__file__).parent / "static"


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


# ── Periodic status push to HUD (every 15 s) ─────────────────────────────────
async def _status_broadcaster():
    while True:
        await asyncio.sleep(15)
        if manager.hud_count == 0:
            continue
        s = get_current_status()
        await manager.broadcast_hud({
            "type": "status",
            "data": {
                "battery_percent": s.battery_percent,
                "battery_plugged":  s.battery_plugged,
                "cpu_percent":      s.cpu_percent,
                "memory_percent":   s.memory_percent,
                "alerts":           s.alerts,
                "remotes_online":   manager.remote_count,
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


# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "online", "system": "JARVIS", "version": "1.0.0",
            "simulation_mode": _SIMULATION, "hud": "/hud", "remote": "/remote"}

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

            elif msg_type == "remote_status":
                # Phone's own battery/sensor data → forward to HUD
                await manager.broadcast_hud({"type": "remote_status", **data})

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager._safe_remove(ws)
        if device == "remote":
            await manager.broadcast_hud({"type": "remote_disconnected",
                                         "count": manager.remote_count})
