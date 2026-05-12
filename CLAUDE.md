# J.A.R.V.I.S — Mission Log (CLAUDE.md)

> Claude: Read this before every task. Update [Current Progress] at the end of every Phase.

---

## Infrastructure

| Item | Value |
|---|---|
| Oracle Server IP | `158.180.78.104` |
| Port | `8000` |
| Codespace Disk | ~31 GB total, ~19 GB free (as of Phase 11) |
| Codespace RAM | 7.8 GB total |
| Stack | Python FastAPI + WebSocket backend · Vanilla HTML/JS HUD + Remote · Expo React Native mobile scaffold |
| Primary branch | `main` |
| Git user | `xnye1` |

---

## Architecture Overview

```
frontend/
  mobile/App.js           Phase 11.1 scaffold — expo-battery + expo-calendar (inactive)
backend/
  main.py                 Entry point: app setup · lifespan · WS hub · page routes ONLY (~254L)
  api/
    models.py             All Pydantic request/response models
    routes.py             All REST endpoints as APIRouter (mounted by main.py)
  core/
    state.py              Singleton global state — energy_saving, counters, service flags
    dispatcher.py         Priority-queue HUD notification dispatcher (CRITICAL→LOW)
    persona.py            Hybrid-Adaptive JarvisPersona — Claude API + simulation fallback
    memory.py             Redis-backed conversation history (local fallback)
    system_info.py        psutil telemetry: CPU / MEM / DISK / Battery
  services/
    __init__.py           ServiceRegistry — collective start/stop, name lookup
    base_service.py       Abstract base class for all services (lifecycle + emit helpers)
    memory_service.py     Vector-DB-ready semantic memory (local dict → ChromaDB → Pinecone)
    dopamine_guard.py     Focus session monitor — psutil process scan + Pomodoro timer
    study_service.py      Study coach — D-Day countdown, Pomodoro tracker, quiz, heatmap (mock → Phase 16)
    productivity_service.py  Task board, goal streaks, schedule gaps, focus score (mock → Phase 16)
    intelligence_service.py  Personal search, auto-summariser, advice, news digest (mock → Phase 16)
    system_service.py     Code review advisor, automations, dep scanner, shell advice (mock → Phase 16)
    analysis_service.py   Mood/stress detector, behaviour patterns, cognitive load (mock → Phase 16)
  system/
    monitor.py            ProactiveEngine — threshold alerts (battery/CPU/MEM)
  static/
    hud.html              Laptop HUD — Widget Registry pattern (reg() per event type)
    remote.html           Virtual controller — chat, reactor, calendar, focus session
```

### Notification Priority (dispatcher.py)
| Level | int | Usage |
|---|---|---|
| CRITICAL | 1 | Deploy failures, security alerts |
| HIGH | 2 | JARVIS AI responses, proactive alerts, dopamine warnings |
| NORMAL | 3 | Telemetry, calendar sync, test signals |
| LOW | 4 | Service online notices, soft info |

### HUD Widget Registry (hud.html)
New event types are added as `reg('type', handler)` — zero changes to WS connect() logic.
Current handlers: connected · pong · remote_speaking · chat_response · proactive_alert · orb_react · status · remote_status · remote_connected · remote_disconnected · test_signal · gigagenie_command · reactor_state · system_update · deploy_failed · calendar_data · service_online · dopamine_session_start · dopamine_session_end · dopamine_alert · **study_update · productivity_update · intelligence_update · sysassist_update · analysis_update** (→ service status bar chips)

### WebSocket Protocol

| Message type | Direction | Description |
|---|---|---|
| `register` | client→server | First message; sets `device: "hud"` or `"remote"` |
| `chat` | remote→server | Text command |
| `chat_response` | server→client | JARVIS reply |
| `remote_status` | remote→server | Phone/virtual battery data |
| `reactor_toggle` | client→server | Toggle energy-saving mode |
| `reactor_state` | server→all | Broadcasts new reactor state |
| `calendar_data` | remote→server→hud | Today's schedule |
| `test_signal` | either→server | Signal test |
| `ping` / `pong` | either | Latency measurement |
| `status` | server→hud | Periodic telemetry (CPU/MEM/DISK/etc.) |
| `study_update` | server→hud | Study service mock report (10 s) |
| `productivity_update` | server→hud | Productivity service mock report (10 s) |
| `intelligence_update` | server→hud | Intelligence service mock report (10 s) |
| `sysassist_update` | server→hud | System service mock report (10 s) |
| `analysis_update` | server→hud | Analysis service mock report (10 s) |

### REST Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health root |
| GET | `/health` | Status JSON |
| POST | `/chat` | Text chat |
| GET | `/status` | System status snapshot |
| POST | `/reset` | Clear conversation |
| POST | `/gigagenie` | GiGA Genie voice bridge |
| POST | `/reactor` | Set `{energy_saving: bool}` |
| GET | `/reactor` | Current reactor state |
| POST | `/calendar` | Push calendar events to HUD |
| GET | `/telemetry` | Full psutil snapshot |
| GET | `/hud` | HUD HTML page |
| GET | `/remote` | Remote HTML page |

---

## Reactor / Energy Mode

| Mode | Poll interval | HUD animations |
|---|---|---|
| **FULL POWER** (reactor ON) | 5 s | 60 FPS — all CSS animations active |
| **ENERGY SAVING** (reactor OFF) | 600 s (10 min) | Static — `body.static-mode` class kills all CSS animations |

Toggle via: HUD switch · Remote switch · `POST /reactor` · WS `reactor_toggle` message.

---

## Current Progress

### Completed Phases

| Phase | Description | Key Files |
|---|---|---|
| 1–3 | Core architecture, FastAPI, persona, memory | `backend/main.py`, `core/persona.py`, `core/memory.py` |
| 4–6 | System monitor, proactive engine, alerts | `system/monitor.py` |
| 7 | Dual-interface HUD + Remote cross-link | `static/hud.html`, `static/remote.html` |
| 8 (stub) | GiGA Genie bridge | `POST /gigagenie` in `main.py` |
| 9 | HUD starship redesign, orb animation, test signal | `static/hud.html` full redesign |
| 11.1 | Mobile Expo scaffold (expo-battery + expo-calendar) | `frontend/mobile/App.js` |
| 11.2 | Laptop-centric cockpit: local telemetry + reactor + calendar | `core/system_info.py`, updated `main.py`/`hud.html`/`remote.html` |
| 12 | Data Cascade animation — dual side-panel hex streams, 30FPS, reactor sync | `static/hud.html` — `DataCascade` class, canvas#cascade-l/r |
| 14 | CI/CD pipeline — GitHub Actions auto-deploy + HUD webhook notification | `.github/workflows/deploy.yml`, `POST /deploy-notify` in `main.py` |
| Persona | Hybrid-Adaptive identity engine — 4-mode behavioral matrix, domain awareness | `core/persona.py` — `IDENTITY` dict, `SYSTEM_PROMPT`, `GET /persona` |
| 15 | Modular architecture — state singleton, priority dispatcher, service layer, Widget Registry HUD | `core/state.py`, `core/dispatcher.py`, `services/`, `api/`, `main.py` refactor |
| 15 (skeleton) | 5 mock service files, 10-s broadcaster, HUD service status bar (5 chips) | `services/study|productivity|intelligence|system|analysis_service.py`, `main.py _mock_service_broadcaster`, `hud.html #svc-bar` |

### Phase 11.2 Deliverables (latest)

- **`backend/core/system_info.py`** — `get_detailed_status()` returns CPU/MEM/DISK/Battery via psutil; confirmed live on codespace.
- **`backend/main.py`** — Added: `energy_saving_mode` global; `POST/GET /reactor`; `POST /calendar`; `GET /telemetry`; WS handlers for `reactor_toggle` + `calendar_data`; broadcaster now dynamic (5 s vs 600 s); disk data included in status push.
- **`backend/static/hud.html`** — Added: DISK bar in SERVER TELEMETRY panel; NEURAL CALENDAR widget in right column; REACTOR CONTROL toggle switch; `body.static-mode` CSS (kills all animations in energy-save mode); `applyReactorState()`, `renderCalendar()`, `toggleReactor()` JS functions; WS handlers for `reactor_state` + `calendar_data`.
- **`backend/static/remote.html`** — Added: REACTOR POWER toggle strip; NEURAL CALENDAR section with editable event list + SYNC button; `pushCalendar()`, `toggleReactor()`, `applyReactorUI()` JS; WS handler for `reactor_state`.
- **`frontend/mobile/App.js`** — Expo scaffold with `useBattery()`, `useTodayEvents()`, `useReactor()` hooks; calendar permission request; battery → server at dynamic interval; calendar push via WS.

### Phase 14 Deliverables (latest)

- **`.github/workflows/deploy.yml`** — push to `main` → SSH into Oracle → `git pull` + `docker compose up -d --build` + `docker image prune` → curl `/deploy-notify` → HUD notification. `concurrency` guard prevents parallel deploys.
- **`backend/main.py`** — `POST /deploy-notify` validates `X-Deploy-Token` header vs `DEPLOY_WEBHOOK_SECRET` env var, broadcasts `system_update` or `deploy_failed` WS event to all HUD clients.
- **`backend/static/hud.html`** — `showDeployBanner()` pops a 8-second floating banner; WS cases for `system_update` (cyan) and `deploy_failed` (red).
- **`.env.example`** — added `DEPLOY_WEBHOOK_SECRET` entry.

**Required GitHub Secrets** (see setup guide below):
`SERVER_IP`, `SERVER_USER`, `SSH_PRIVATE_KEY`, `DEPLOY_WEBHOOK_SECRET`

### Pending / Next Phase

- Phase 10 (if planned): TBD
- iOS device re-integration when available: activate `frontend/mobile/App.js`
- GiGA Genie full integration (Phase 8 was stubbed)
- Redis conversation memory (currently disabled/fallback)
- ANTHROPIC_API_KEY activation (currently simulation mode)

---

## Environment Variables (`.env`)

```
ANTHROPIC_API_KEY=sk-ant-...    # Set to activate full AI mode
BATTERY_WARN_THRESHOLD=20
CPU_WARN_THRESHOLD=85
MEMORY_WARN_THRESHOLD=90
PROACTIVE_POLL_INTERVAL=30
```

---

## Run Commands

```bash
# Install deps
pip install -r requirements.txt

# Start server (from /workspaces/J.A.R.V.I.S/backend)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# HUD browser
open http://158.180.78.104:8000/hud

# Remote controller
open http://158.180.78.104:8000/remote
```
