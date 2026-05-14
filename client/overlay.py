"""
client/overlay.py — J.A.R.V.I.S. v3.0  WebEngine Hybrid HUD

Full-screen immersive HUD via QWebEngineView + QWebChannel:
  - Glassmorphism panels (backdrop-filter: blur)
  - Arc reactor Canvas animation (rotation speed ∝ FocusScore)
  - Focus waveform, bio gauges, glitch search results — all CSS/JS
  - Sound sync: simpleaudio servo-click on important events
  - QWebChannel bridge: <1 ms Python→JS dispatch latency

Thread model:
  Qt main thread  — QWebEngineView + QWebChannel
  asyncio thread  — WebSocket receive loop → _bridge.signal.emit (thread-safe)
  daemon thread   — HotwordEngine → _bridge.wakeTriggered.emit (thread-safe)

Requires: pip install PyQt6-WebEngine
"""

from __future__ import annotations

import os

# ── GPU / driver flags — MUST be set before any Qt module is imported ──────────
# --disable-gpu            : disable hardware GPU acceleration
# --in-process-gpu         : run GPU code inside renderer (no separate GPU subprocess)
#                            prevents the GPU IPC channel from crashing on GLES init
# --disable-gpu-compositing: use CPU-based compositing instead of GPU compositing
# --disable-webgl          : our Canvas 2D HUD does not need WebGL; avoids GPU init
# --disable-dev-shm-usage  : use /tmp instead of /dev/shm (Linux VMs / WSL)
# --no-sandbox             : already covered by QTWEBENGINE_DISABLE_SANDBOX
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu "
    "--in-process-gpu "
    "--disable-gpu-compositing "
    "--disable-webgl "
    "--disable-dev-shm-usage "
    "--no-sandbox",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# ── High DPI / scaling — must be set before QApplication ─────────────────────
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING",   "1")
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import json
import logging
import pathlib
import signal as _signal
import time
import threading
import webbrowser
from typing import Any

try:
    from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal, pyqtSlot
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6 import sip as _sip
    _HAS_ENGINE = True
    _HAS_SIP    = True
except ImportError as _ie:
    _HAS_ENGINE = False
    _HAS_SIP    = False
    # Stubs so module-level code still parses without Qt installed
    QObject = object  # type: ignore[assignment,misc]
    Qt      = None    # type: ignore[assignment]
    _sip    = None    # type: ignore[assignment]
    def pyqtSignal(*a, **k): return None  # type: ignore[misc]
    def pyqtSlot(*a, **k):                # type: ignore[misc]
        def _d(f): return f
        return _d

log = logging.getLogger("jarvis.overlay")

_HUD_PATH = pathlib.Path(__file__).parent / "hud_v3.html"

_STAGE_COLORS: dict[str, str] = {
    "GENTLE":    "#39FF14",
    "SARCASTIC": "#FFFF00",
    "STERN":     "#FFBB00",
    "FURIOUS":   "#FF6B35",
    "LOCKDOWN":  "#FF003C",
}

# Last received shop results — checked on clap-wake
_last_shop:   list[dict[str, Any]] = []
_bridge_gone: bool = False  # set True by QApplication.aboutToQuit signal
_hud_ref:     "WebHUD | None" = None  # prevents GC of the QWebEngineView window


def _emit(sig: str, *args) -> None:
    """Safely emit a named signal on _bridge from any thread.

    Accepts the signal attribute NAME as a string (e.g. "logLine") so that
    Python's argument-evaluation never touches a potentially-deleted QObject
    before entering this function's try/except block.

    Guard order:
      1. _bridge is None    — not yet initialized (QApplication not ready)
      2. _bridge_gone flag  — cheapest check; set on app aboutToQuit
      3. sip.isdeleted()    — C++ object already freed
      4. except RuntimeError — any remaining race between check and emit
    """
    if _bridge is None or _bridge_gone:
        return
    try:
        if _HAS_SIP and _sip.isdeleted(_bridge):  # type: ignore[arg-type]
            return
        getattr(_bridge, sig).emit(*args)
    except (RuntimeError, AttributeError):
        pass


# ── QWebChannel Data Bridge ────────────────────────────────────────────────────

class JarvisDataBridge(QObject):
    """
    Exposed to JS as 'jarvis'.
    Signals go Python → JS (via QWebChannel subscription).
    Slots go JS → Python (callable from JS).
    """

    # ── Python → JS ───────────────────────────────────────────────
    angerUpdated   = pyqtSignal(float, str, str)       # gauge, stage, hud_color
    focusScored    = pyqtSignal(float)
    sysUpdated     = pyqtSignal(float, float, float)   # cpu, mem, disk
    logLine        = pyqtSignal(str, bool)             # text, important
    speakStarted   = pyqtSignal(str, str, int)         # text, importance, duration_ms
    speakFinished  = pyqtSignal()
    alertFlashed   = pyqtSignal(str)
    agentFetching  = pyqtSignal(bool)
    searchReady    = pyqtSignal('QVariantList')         # web results
    shopReady      = pyqtSignal('QVariantList')         # shop results
    ghostToggled   = pyqtSignal(bool)
    flickering     = pyqtSignal(bool)
    wakeTriggered  = pyqtSignal(str)
    errorFlash     = pyqtSignal(str, str)              # level, message
    heartbeatLost  = pyqtSignal()
    heartbeatOk    = pyqtSignal()
    offlineMode    = pyqtSignal(bool)
    pingMs         = pyqtSignal(int)
    bootDuration   = pyqtSignal(int)     # boot.wav duration in ms → JS sync

    # ── JS → Python ───────────────────────────────────────────────

    @pyqtSlot()
    def hud_ready(self) -> None:
        log.info("[Bridge] JS HUD ready.")
        # Emit boot sound duration so JS can sync animation step timing
        if _boot_duration_ms > 0:
            self.bootDuration.emit(_boot_duration_ms)
        # Start playback (no-op if file was missing or audio init failed)
        if _boot_player is not None:
            try:
                _boot_player.play()
            except Exception as exc:
                log.warning("[Boot] play() failed: %s", exc)

    @pyqtSlot(str)
    def open_url(self, url: str) -> None:
        if url.startswith("http"):
            webbrowser.open(url)

    @pyqtSlot()
    def play_servo(self) -> None:
        """Generate a short mechanical servo click and play it instantly."""
        try:
            import numpy as np
            import simpleaudio as sa
            sr = 44100
            t  = np.linspace(0, 0.038, int(sr * 0.038), False)
            wave = (
                np.sin(2 * np.pi * 3400 * t) * 0.45 * np.exp(-t * 90)
            ).astype(np.float32)
            pcm = (wave * 32767).astype(np.int16)
            sa.play_buffer(pcm, 1, 2, sr)
        except Exception:
            pass  # non-critical

    @pyqtSlot(str)
    def send_command(self, text: str) -> None:
        """JS command bar → POST /chat on the asyncio thread."""
        text = text.strip()
        if not text:
            return
        _emit("logLine", f"USER  {text[:60]}", True)
        if _ws_loop is not None:
            asyncio.run_coroutine_threadsafe(_post_chat(text), _ws_loop)


# _bridge is initialized in JarvisOverlay.run() AFTER QApplication is created.
# Creating a QObject before QApplication exists is undefined behaviour in Qt.
_bridge:          JarvisDataBridge | None = None
_sys_state:       dict[str, float] = {"cpu": 0.0, "mem": 0.0, "disk": 0.0, "focus": 50.0}
_ws_loop:         "asyncio.AbstractEventLoop | None" = None
_http_base_url:   str  = "http://158.180.78.104:8000"
_ping_state:      dict = {"t0": 0.0, "pending": False}
_boot_duration_ms: int = 0      # WAV duration; 0 = no file / unreadable
_boot_player:     Any  = None   # QMediaPlayer ref — kept alive here to avoid GC
_boot_audio_out:  Any  = None   # QAudioOutput  ref — same reason


# ── Boot audio helpers ─────────────────────────────────────────────────────────

_BOOT_WAV = pathlib.Path(__file__).parent.parent / "assets" / "boot.wav"


def _get_wav_duration_ms(path: pathlib.Path) -> int:
    """Read WAV duration via stdlib (synchronous, no Qt needed). Returns 0 on error."""
    try:
        import wave as _wave
        with _wave.open(str(path), "rb") as wf:
            return int(wf.getnframes() / wf.getframerate() * 1000)
    except Exception:
        return 0


def _init_boot_audio() -> None:
    """Prepare QMediaPlayer for boot.wav. Must be called AFTER QApplication exists."""
    global _boot_duration_ms, _boot_player, _boot_audio_out
    if not _HAS_ENGINE:
        return

    # Duration is read synchronously — no Qt media pipeline needed
    _boot_duration_ms = _get_wav_duration_ms(_BOOT_WAV)

    try:
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

        _boot_audio_out = QAudioOutput()
        _boot_audio_out.setVolume(0.82)

        _boot_player = QMediaPlayer()
        _boot_player.setAudioOutput(_boot_audio_out)

        if _BOOT_WAV.exists():
            _boot_player.setSource(
                QUrl.fromLocalFile(str(_BOOT_WAV.resolve()))
            )
            log.info("[Boot] Audio armed: %s  (%d ms)", _BOOT_WAV.name, _boot_duration_ms)
        else:
            log.info("[Boot] assets/boot.wav not found — audio skipped")
            _boot_player   = None
            _boot_audio_out = None

    except Exception as exc:
        log.warning("[Boot] Audio init failed: %s", exc)
        _boot_player    = None
        _boot_audio_out = None


# ── WebHUD ─────────────────────────────────────────────────────────────────────

class WebHUD:
    """Full-screen QWebEngineView wrapper. Zero QPainter — all rendering in JS."""

    def __init__(self, audio_response: Any = None) -> None:
        if not _HAS_ENGINE:
            raise ImportError(
                "PyQt6-WebEngine is not installed.\n"
                "Run: pip install PyQt6-WebEngine"
            )

        self._audio = audio_response
        self._view  = QWebEngineView()

        self._view.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        # Construct QWebEnginePage DIRECTLY instead of calling view.page().
        # On Python 3.14 + PyQt6-WebEngine, Chromium's renderer subprocess spawn
        # triggers a Windows console-ctrl event that Python 3.14 converts to
        # KeyboardInterrupt.  Temporarily suppressing SIGINT during construction
        # prevents the interrupt from propagating into Python.
        _old_sigint = _signal.signal(_signal.SIGINT, _signal.SIG_IGN)
        try:
            self._page = QWebEnginePage(self._view)
        finally:
            _signal.signal(_signal.SIGINT, _old_sigint)

        s = self._page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)

        # Wire QWebChannel to the explicit page, then install it into the view
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("jarvis", _bridge)
        self._page.setWebChannel(self._channel)
        self._view.setPage(self._page)

        # Transparent background — lets backdrop-filter:blur() work correctly
        # and prevents a white/grey flash before the HTML background renders.
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._page.setBackgroundColor(QColor(0, 0, 0, 0))

        # Load HUD HTML
        if not _HUD_PATH.exists():
            log.error("[WebHUD] HUD file missing: %s", _HUD_PATH)
        else:
            self._view.load(QUrl.fromLocalFile(str(_HUD_PATH.resolve())))
            log.info("[WebHUD] Loaded %s", _HUD_PATH)

        # Track shop results for clap-wake confirm
        _bridge.shopReady.connect(self._on_shop_ready)

        # Wake handler
        _bridge.wakeTriggered.connect(self._on_wake)

    def _on_shop_ready(self, results: list) -> None:
        global _last_shop
        _last_shop = list(results) if results else []

    def _on_wake(self, event_name: str) -> None:
        """Hotword or clap wake event — runs on Qt main thread."""
        # Clap + pending shop results → confirm cart
        if event_name == "clap" and _last_shop:
            url = _last_shop[0].get("url", "")
            if url:
                webbrowser.open(url)
                _bridge.speakStarted.emit("Proceeding to cart.", "normal", 1800)
                _bridge.logLine.emit("SHOP  open → top result", True)
                return

        # Normal wake-up sequence
        if self._audio is not None:
            try:
                self._audio.play()
            except Exception:
                pass

        try:
            from client.hotword import os_wake_display
            os_wake_display()
        except Exception:
            pass

        _bridge.ghostToggled.emit(False)
        label = "자비스" if event_name == "hotword" else "박수"
        _bridge.logLine.emit(f"WAKE  [{label}]  JARVIS activated", True)
        _bridge.speakStarted.emit("Yes, Sir.", "critical", 1800)
        _bridge.flickering.emit(True)
        QTimer.singleShot(1800, lambda: _bridge.flickering.emit(False))

    def awaken(self) -> None:
        """Cover the full screen (including taskbar) and start boot."""
        screen = QApplication.primaryScreen()
        if screen:
            self._view.setGeometry(screen.geometry())
        self._view.showFullScreen()


# ── Async search helpers ───────────────────────────────────────────────────────

async def _do_web_search(query: str, agent: Any, prefs: Any) -> None:
    _emit("agentFetching", True)
    _emit("logLine", f"SEARCH  '{query[:35]}'…", False)
    try:
        results     = await agent.search(query)
        focus_score = _sys_state.get("focus", 50.0)
        ranked      = prefs.rank_results(results, focus_score, query)
        intro       = prefs.briefing_intro(focus_score)
        payload     = [
            {"title":   getattr(r, "title",   ""),
             "snippet": getattr(r, "snippet", ""),
             "url":     getattr(r, "url",     ""),
             "rank":    i}
            for i, r in enumerate(ranked)
        ]
        _emit("searchReady", payload)
        _emit("speakStarted",
              f"{intro}검색 완료 — {len(ranked)}개 결과", "normal", 2200)
        _emit("logLine", f"SEARCH  done  {len(ranked)} results", True)
    except Exception as exc:
        log.error("[Agent] web search failed: %s", exc)
    finally:
        _emit("agentFetching", False)


async def _do_shop_search(keyword: str, agent: Any, prefs: Any) -> None:
    _emit("agentFetching", True)
    _emit("logLine", f"SHOP  '{keyword[:35]}'…", False)
    try:
        results     = await agent.shop(keyword)
        focus_score = _sys_state.get("focus", 50.0)
        ranked      = prefs.rank_results(results, focus_score, keyword)
        payload     = [
            {"title":         getattr(r, "title",         ""),
             "price":         getattr(r, "price",         0),
             "price_str":     r.price_str() if hasattr(r, "price_str") else "",
             "delivery_days": getattr(r, "delivery_days", None),
             "url":           getattr(r, "url",           ""),
             "rank":          i}
            for i, r in enumerate(ranked)
        ]
        _emit("shopReady", payload)
        if ranked:
            top     = ranked[0]
            price_s = top.price_str() if hasattr(top, "price_str") else ""
            _emit("speakStarted",
                  f"최저가 {price_s} — 박수로 장바구니 담기", "normal", 3000)
        _emit("logLine", f"SHOP  done  {len(ranked)} items", True)
    except Exception as exc:
        log.error("[Agent] shop search failed: %s", exc)
    finally:
        _emit("agentFetching", False)


# ── Async helpers ─────────────────────────────────────────────────────────────

async def _post_chat(text: str) -> None:
    """POST a command to /chat using stdlib only (no aiohttp dep)."""
    import urllib.request
    import urllib.error
    payload = json.dumps({"message": text}).encode()
    def _do() -> None:
        req = urllib.request.Request(
            f"{_http_base_url}/chat",
            data    = payload,
            headers = {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8):
            pass
    try:
        await asyncio.get_event_loop().run_in_executor(None, _do)
    except Exception as exc:
        log.warning("[Overlay] POST /chat failed: %s", exc)
        _emit("logLine", "CHAT  server unreachable", False)


async def _local_poll_loop() -> None:
    """Emit local psutil telemetry while the Oracle server is unreachable."""
    try:
        import psutil
    except ImportError:
        _emit("logLine", "OFFLINE  psutil unavailable", False)
        return
    disk_path = "C:\\" if os.name == "nt" else "/"
    while True:
        try:
            cpu  = psutil.cpu_percent(interval=None)
            mem  = psutil.virtual_memory().percent
            try:
                disk = psutil.disk_usage(disk_path).percent
            except Exception:
                disk = 0.0
            _sys_state.update(cpu=cpu, mem=mem, disk=disk)
            _emit("sysUpdated", cpu, mem, disk)
        except Exception:
            pass
        await asyncio.sleep(5)


async def _ping_loop(ws: Any) -> None:
    """Send application-level pings every 10 s; latency read from pong handler."""
    while True:
        await asyncio.sleep(10)
        try:
            _ping_state["t0"]      = time.monotonic()
            _ping_state["pending"] = True
            await ws.send(json.dumps({"type": "ping"}))
        except Exception:
            break


# ── WebSocket receive loop ─────────────────────────────────────────────────────

async def _ws_receive_loop(ws_url: str, http_base: str) -> None:
    from client.focus_score       import FocusScoreEngine
    from client.predictive_alert  import PredictiveAlert
    from client.search_agent      import SearchAgent
    from client.preference_engine import PreferenceEngine
    from client.diagnostics       import HeartbeatMonitor
    import websockets  # type: ignore[import-untyped]

    focus_engine = FocusScoreEngine(
        on_ghost_mode=lambda v: _emit("ghostToggled", v),
        http_base=http_base,
    )
    predictor = PredictiveAlert(http_base=http_base)
    asyncio.create_task(predictor.run())

    search_agent = SearchAgent()
    pref_db      = pathlib.Path.home() / ".jarvis" / "jarvis.db"
    prefs        = PreferenceEngine(pref_db)

    heartbeat = HeartbeatMonitor(
        http_base   = http_base,
        on_lost     = lambda: _emit("heartbeatLost"),
        on_restored = lambda: _emit("heartbeatOk"),
    )
    asyncio.create_task(heartbeat.run())

    offline_poll_task: "asyncio.Task | None" = None
    backoff = 2.0
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                # ── Online: cancel local poller, clear offline banner ──
                if offline_poll_task is not None:
                    offline_poll_task.cancel()
                    offline_poll_task = None
                _emit("offlineMode", False)

                log.info("[Overlay] WS connected → %s", ws_url)
                await ws.send(json.dumps({"type": "register", "device": "overlay"}))
                _emit("logLine", f"WS connected  {ws_url}", True)
                backoff = 2.0

                ping_task = asyncio.create_task(_ping_loop(ws))
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        mtype = msg.get("type", "")

                        # All emit() calls dispatch to Qt main thread via QueuedConnection.
                        # RuntimeError = bridge QObject destroyed (app shutting down).
                        try:
                            if mtype == "pong":
                                if _ping_state["pending"]:
                                    ms = int((time.monotonic() - _ping_state["t0"]) * 1000)
                                    _ping_state["pending"] = False
                                    _emit("pingMs", ms)

                            elif mtype == "anger_update":
                                gauge = float(msg.get("gauge", 0.0))
                                stage = str(msg.get("stage", "GENTLE"))
                                color = str(msg.get("hud_color",
                                                    _STAGE_COLORS.get(stage, "#39FF14")))
                                _emit("angerUpdated", gauge, stage, color)
                                focus_engine.record_gauge(gauge)
                                score = focus_engine.current_score
                                _sys_state["focus"] = score
                                _emit("focusScored", score)

                            elif mtype == "study_update":
                                data    = msg.get("data", {})
                                subject = str(data.get("subject", ""))
                                pct     = float(data.get("progress", 0.0))
                                if subject:
                                    _emit("logLine",
                                          f"STUDY  {subject}  {pct:.0f}%", False)

                            elif mtype == "status":
                                cpu  = float(msg.get("cpu_percent",  0.0))
                                mem  = float(msg.get("mem_percent",  0.0))
                                disk = float(msg.get("disk_percent", 0.0))
                                _sys_state.update(cpu=cpu, mem=mem, disk=disk)
                                _emit("sysUpdated", cpu, mem, disk)

                            elif mtype == "calendar_data":
                                predictor.update_events(msg.get("events", []))

                            elif mtype == "location_update":
                                lat = float(msg.get("lat", 0.0))
                                lon = float(msg.get("lon", 0.0))
                                if lat or lon:
                                    predictor.update_location(lat, lon)

                            elif mtype == "stealth_update":
                                muted  = bool(msg.get("muted", False))
                                reason = str(msg.get("reason", ""))
                                _emit("logLine",
                                      f"STEALTH  {'ON' if muted else 'OFF'}  {reason}",
                                      False)

                            elif mtype == "chat_response":
                                text = str(msg.get("message", msg.get("response", "")))
                                if text:
                                    duration = max(2200, len(text) * 55)
                                    _emit("speakStarted", text, "normal", duration)

                            elif mtype == "proactive_alert":
                                text     = str(msg.get("message", ""))
                                severity = str(msg.get("severity", "NORMAL")).upper()
                                imp = (
                                    "critical" if severity == "CRITICAL"
                                    else "warning" if severity in ("HIGH", "WARNING")
                                    else "normal"
                                )
                                duration = max(3000, len(text) * 60)
                                _emit("speakStarted", text, imp, duration)
                                _emit("alertFlashed", text)
                                _emit("logLine", f"ALERT  {text[:60]}", True)

                            elif mtype == "search_request":
                                query = str(msg.get("query", "")).strip()
                                if query:
                                    asyncio.create_task(
                                        _do_web_search(query, search_agent, prefs))

                            elif mtype == "shop_request":
                                keyword = str(
                                    msg.get("keyword", msg.get("query", ""))).strip()
                                if keyword:
                                    asyncio.create_task(
                                        _do_shop_search(keyword, search_agent, prefs))

                            elif mtype == "feedback":
                                try:
                                    prefs.record_feedback(
                                        query         = str(msg.get("query",        "")),
                                        result_title  = str(msg.get("result_title", "")),
                                        result_domain = str(msg.get("domain",       "")),
                                        positive      = bool(msg.get("positive",    True)),
                                    )
                                except Exception as fb_exc:
                                    log.debug("[Agent] feedback: %s", fb_exc)

                            elif mtype == "deploy_failed":
                                _emit("logLine", "DEPLOY FAILED ⚠", True)
                                _emit("errorFlash", "ERROR", "Deploy pipeline failed")

                            elif mtype == "system_update":
                                _emit("logLine", "DEPLOY  push complete ✓", True)

                        except RuntimeError:
                            log.debug(
                                "[Overlay] Bridge destroyed — dropped msg type=%r", mtype)
                finally:
                    ping_task.cancel()

        except Exception as exc:
            # ── Offline: start local poller if not already running ──
            if offline_poll_task is None or offline_poll_task.done():
                _emit("offlineMode", True)
                _emit("logLine", "OFFLINE MODE — local diagnostics only", True)
                offline_poll_task = asyncio.create_task(_local_poll_loop())

            log.warning(
                "[Overlay] WS error: %s — retry in %.0fs", exc, backoff)
            _emit("logLine", f"WS reconnect in {backoff:.0f}s", False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _run_ws_thread(ws_url: str, http_base: str) -> None:
    global _ws_loop, _http_base_url
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _ws_loop       = loop
    _http_base_url = http_base
    loop.run_until_complete(_ws_receive_loop(ws_url, http_base))


# ── Entry point ────────────────────────────────────────────────────────────────

class JarvisOverlay:
    """Creates QApplication, shows WebHUD fullscreen, starts WS + hotword threads."""

    def __init__(
        self,
        ws_url:    str = "ws://158.180.78.104:8000/ws",
        http_base: str = "http://158.180.78.104:8000",
    ) -> None:
        self._ws_url    = ws_url
        self._http_base = http_base

    def run(self) -> None:
        import sys

        # Must be set BEFORE QApplication — required for Qt WebEngine on Windows
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("JARVIS")

        # QApplication now exists — safe to create QObject subclasses
        global _bridge
        _bridge = JarvisDataBridge()

        # Prepare boot.wav player (needs QApplication; reads WAV duration via stdlib)
        _init_boot_audio()

        # Diagnostics: error logger + HUD bridge
        from client.diagnostics import setup_diagnostics
        setup_diagnostics(
            log_dir  = pathlib.Path("logs"),
            on_error = lambda level, msg: _bridge.errorFlash.emit(level, msg),
        )

        # Audio: preload so play() is <5 ms on first wake
        from client.audio_response import AudioResponse
        audio = AudioResponse()
        audio.preload()

        # WebSocket receiver thread — start before event loop
        ws_url, http_base = self._ws_url, self._http_base
        ws_thread = threading.Thread(
            target = _run_ws_thread,
            args   = (ws_url, http_base),
            daemon = True,
            name   = "jarvis-ws-overlay",
        )
        ws_thread.start()

        # Hotword engine (daemon thread)
        from client.hotword import HotwordEngine
        hotword = HotwordEngine(
            on_wake    = lambda evt: _bridge.wakeTriggered.emit(evt.value),
            power_save = False,
        )
        hotword.start()

        # Defer WebHUD creation to after the event loop starts.
        # QWebEngineView spawns the Chromium renderer process, which needs
        # the Qt event loop running to complete its IPC handshake on Windows.
        # Python 3.14 converts the Chromium subprocess-spawn signal to
        # KeyboardInterrupt — suppress SIGINT for the entire WebHUD init block.
        def _init_hud() -> None:
            global _hud_ref
            _sig = _signal.signal(_signal.SIGINT, _signal.SIG_IGN)
            try:
                hud = WebHUD(audio_response=audio)
            finally:
                _signal.signal(_signal.SIGINT, _sig)
            _hud_ref = hud  # pin to module scope — prevents Python GC from
                            # destroying the QWebEngineView and closing the window
            log.info("[Overlay] Awakening sequence start.")
            hud.awaken()

        def _on_quit() -> None:
            global _bridge_gone, _bridge, _hud_ref
            _bridge_gone = True
            _bridge = None   # prevent any post-destroy signal emission
            _hud_ref = None  # allow GC after Qt has already cleaned up

        app.aboutToQuit.connect(_on_quit)

        QTimer.singleShot(0, _init_hud)
        sys.exit(app.exec())
