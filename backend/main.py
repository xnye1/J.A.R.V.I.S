"""
JARVIS — FastAPI backend entry point.
Run:  uvicorn main:app --reload
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Docker: .env is at project root (one level above backend/)
# Local dev: also works via uvicorn run from backend/
_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=_env_path)

# ── Mode detection (never panic — always degrade gracefully) ──────────────────
_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_SIMULATION = (
    not _ANTHROPIC_KEY
    or not _ANTHROPIC_KEY.startswith("sk-ant-")
    or "여기에" in _ANTHROPIC_KEY
)

if _SIMULATION:
    print("[JARVIS] Simulation Mode active — neural link pending key injection.")
else:
    print("[JARVIS] API key validated. Full operational mode.")

from core.persona import JarvisPersona
from system.monitor import ProactiveEngine, get_current_status

# ── Globals ───────────────────────────────────────────────────────────────────
jarvis = JarvisPersona()
active_websockets: list[WebSocket] = []


async def broadcast(message: str):
    for ws in list(active_websockets):
        try:
            await ws.send_json({"type": "proactive_alert", "message": message})
        except Exception:
            if ws in active_websockets:
                active_websockets.remove(ws)


async def handle_alert(alert_text: str, _status):
    response = jarvis.proactive_alert(alert_text)
    await broadcast(response)


proactive_engine = ProactiveEngine(on_alert=handle_alert)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    task = asyncio.create_task(proactive_engine.start())
    print("[JARVIS] Proactive Engine online, Sir.")
    yield
    proactive_engine.stop()
    task.cancel()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="JARVIS Core", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    simulation_mode: bool = _SIMULATION
    model: str = "claude-sonnet-4-6"


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "status": "online",
        "system": "JARVIS",
        "version": "1.0.0",
        "simulation_mode": _SIMULATION,
        "message": "Good day, Sir. All systems are nominal.",
    }


@app.get("/health")
async def health():
    return {
        "alive": True,
        "simulation_mode": _SIMULATION,
        "api_key_loaded": not _SIMULATION,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty, Sir.")
    reply = jarvis.chat(req.message)
    return ChatResponse(response=reply)


@app.get("/status")
async def system_status():
    s = get_current_status()
    return {
        "battery_percent": s.battery_percent,
        "battery_plugged": s.battery_plugged,
        "cpu_percent": s.cpu_percent,
        "memory_percent": s.memory_percent,
        "alerts": s.alerts,
    }


@app.post("/reset")
async def reset_conversation():
    jarvis.reset_conversation()
    return {"status": "conversation reset", "message": "Memory wiped, Sir. Starting fresh."}


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_websockets.append(ws)
    try:
        await ws.send_json({
            "type": "connected",
            "message": "JARVIS online, Sir.",
            "simulation_mode": _SIMULATION,
        })
        while True:
            data = await ws.receive_json()
            if data.get("type") == "chat":
                reply = jarvis.chat(data["message"])
                await ws.send_json({"type": "chat", "message": reply})
            elif data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        if ws in active_websockets:
            active_websockets.remove(ws)
