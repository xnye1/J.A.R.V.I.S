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
# Fixes "Failed to create GLES3 context": forces Chromium to use software
# rasterization instead of GPU acceleration (common on laptops/VMs).
# Note: QSG_RHI_BACKEND is Qt-Quick-only and NOT set here — QWebEngineView
# uses Chromium's own render pipeline, independent of Qt's scene graph.
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-dev-shm-usage --no-sandbox",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import json
import logging
import pathlib
import threading
import webbrowser
from typing import Any

try:
    from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal, pyqtSlot
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


def _emit(sig: str, *args) -> None:
    """Safely emit a named signal on _bridge from any thread.

    Accepts the signal attribute NAME as a string (e.g. "logLine") so that
    Python's argument-evaluation never touches a potentially-deleted QObject
    before entering this function's try/except block.

    Guard order:
      1. _bridge_gone flag  — cheapest check; set on app aboutToQuit
      2. sip.isdeleted()    — C++ object already freed
      3. except RuntimeError — any remaining race between check and emit
    """
    if _bridge_gone:
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

    # ── JS → Python ───────────────────────────────────────────────

    @pyqtSlot()
    def hud_ready(self) -> None:
        log.info("[Bridge] JS HUD ready.")

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


_bridge = JarvisDataBridge()
_sys_state: dict[str, float] = {"cpu": 0.0, "mem": 0.0, "disk": 0.0, "focus": 50.0}


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
        # On Python 3.14 + PyQt6-WebEngine, view.page() triggers an internal
        # SIP type lookup that goes through CPython's EnumType.__call__ and
        # can deadlock or raise before the page object is returned.
        # Creating QWebEnginePage(view) explicitly bypasses this path entirely.
        self._page = QWebEnginePage(self._view)

        s = self._page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)

        # Wire QWebChannel to the explicit page, then install it into the view
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("jarvis", _bridge)
        self._page.setWebChannel(self._channel)
        self._view.setPage(self._page)

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

    backoff = 2.0
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                log.info("[Overlay] WS connected → %s", ws_url)
                await ws.send(json.dumps({"type": "register", "device": "overlay"}))
                _emit("logLine", f"WS connected  {ws_url}", True)
                backoff = 2.0

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    mtype = msg.get("type", "")

                    # All emit() calls here run on the asyncio thread and are
                    # dispatched to the Qt main thread via QueuedConnection.
                    # RuntimeError means the bridge QObject was destroyed
                    # (app shutting down) — silently drop the message.
                    try:
                        if mtype == "anger_update":
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
                                _emit("speakStarted",
                                      text, "normal", duration)

                        elif mtype == "proactive_alert":
                            text     = str(msg.get("message", ""))
                            severity = str(msg.get("severity", "NORMAL")).upper()
                            imp      = (
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
                            _emit("errorFlash",
                                  "ERROR", "Deploy pipeline failed")

                        elif mtype == "system_update":
                            _emit("logLine",
                                  "DEPLOY  push complete ✓", True)

                    except RuntimeError:
                        log.debug(
                            "[Overlay] Bridge destroyed — dropped msg type=%r", mtype)

        except Exception as exc:
            log.warning(
                "[Overlay] WS error: %s — retry in %.0fs", exc, backoff)
            _emit("logLine", f"WS reconnect in {backoff:.0f}s", False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _run_ws_thread(ws_url: str, http_base: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
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
        def _init_hud() -> None:
            hud = WebHUD(audio_response=audio)
            log.info("[Overlay] Awakening sequence start.")
            hud.awaken()

        def _on_quit() -> None:
            global _bridge_gone
            _bridge_gone = True

        app.aboutToQuit.connect(_on_quit)

        QTimer.singleShot(0, _init_hud)
        sys.exit(app.exec())
