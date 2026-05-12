"""
JARVIS — FastAPI entry point.
Responsibilities: app setup · lifespan · WebSocket hub · page routes.
All REST logic lives in api/routes.py. All state in core/state.py.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

_KEY        = os.getenv("ANTHROPIC_API_KEY", "")
_SIMULATION = not _KEY or not _KEY.startswith("sk-ant-")
print(f"[JARVIS] {'Simulation' if _SIMULATION else 'Full'} mode.")

from core.dispatcher  import dispatcher, Priority
from core.persona     import JarvisPersona
from core.state       import state
from core.system_info import get_detailed_status
from services         import ServiceRegistry
from system.monitor   import ProactiveEngine, get_current_status
import api.routes as routes

STATIC = Path(__file__).parent / "static"


# ── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """Tracks HUD and Remote WebSocket connections."""

    def __init__(self) -> None:
        self.hud:    list[WebSocket] = []
        self.remote: list[WebSocket] = []

    def _drop(self, ws: WebSocket) -> None:
        self.hud    = [c for c in self.hud    if c is not ws]
        self.remote = [c for c in self.remote if c is not ws]

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

    async def broadcast_all(self, payload: dict) -> None:
        for ws in list(self.hud + self.remote):
            await self._send(ws, payload)

    @property
    def remote_count(self) -> int: return len(self.remote)

    @property
    def hud_count(self) -> int: return len(self.hud)


# ── Singletons ────────────────────────────────────────────────────────────────

jarvis   = JarvisPersona()
manager  = ConnectionManager()
registry = ServiceRegistry(dispatcher, state)


async def _on_alert(alert_text: str, _status) -> None:
    reply = jarvis.proactive_alert(alert_text)
    await dispatcher.emit({"type": "proactive_alert", "message": reply}, Priority.HIGH)
    await dispatcher.emit({"type": "orb_react", "intensity": 0.9, "duration": 3000}, Priority.HIGH)

proactive_engine = ProactiveEngine(on_alert=_on_alert)


# ── Periodic status push ──────────────────────────────────────────────────────

async def _status_broadcaster() -> None:
    last_at = 0.0
    while True:
        await asyncio.sleep(5)
        interval = 600 if state.energy_saving else 5
        now = time.monotonic()
        if now - last_at < interval or manager.hud_count == 0:
            continue
        last_at = now
        s  = get_detailed_status()
        pa = get_current_status()
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


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    dispatcher.set_broadcast(manager.broadcast_hud)
    routes.wire(manager, registry)

    t1 = asyncio.create_task(proactive_engine.start())
    t2 = asyncio.create_task(_status_broadcaster())
    t3 = asyncio.create_task(dispatcher.run())
    await registry.start_all()

    print("[JARVIS] All systems nominal, Sir.")
    yield

    await registry.stop_all()
    proactive_engine.stop()
    dispatcher.stop()
    for t in (t1, t2, t3):
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
                "simulation_mode": _SIMULATION,
                "status": {
                    "cpu_percent":    s.cpu_percent,
                    "memory_percent": s.memory_percent,
                    "battery_percent": s.battery_percent,
                    "battery_plugged": s.battery_plugged,
                },
            })
        else:
            manager.remote.append(ws)
            await ws.send_json({"type": "connected", "device": "remote",
                                "simulation_mode": _SIMULATION})
            await manager.broadcast_hud({"type": "remote_connected",
                                         "count": manager.remote_count})

        while True:
            data     = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                text = data.get("message", "").strip()
                if not text:
                    continue
                await manager.broadcast_hud({"type": "remote_speaking", "message": text})
                await dispatcher.emit({"type": "orb_react", "intensity": 0.6, "duration": 500},
                                      Priority.NORMAL)
                reply = jarvis.chat(text)
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

            elif msg_type == "reactor_toggle":
                state.energy_saving = bool(data.get("energy_saving", False))
                await manager.broadcast_all({"type": "reactor_state",
                                             "energy_saving": state.energy_saving})

            elif msg_type == "calendar_data":
                await dispatcher.emit({"type": "calendar_data",
                                       "date":   data.get("date", ""),
                                       "events": data.get("events", [])},
                                      Priority.NORMAL)

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

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager._drop(ws)
        if device == "remote":
            await manager.broadcast_hud({"type": "remote_disconnected",
                                         "count": manager.remote_count})
