"""
client/overlay.py — Transparent, click-through PyQt6 overlay.

Sections (top → bottom in one pill widget):
  1. Fury Gauge   — anger stage label + progress bar
  2. Mini-Progress — current subject + goal completion bar

Modes:
  Ghost Mode  : when FocusScoreEngine reports score > 70 (deep focus),
                the overlay fades to 28% opacity so it never distracts.
                Returns to 88% when focus drops (needs more awareness).
  Alert Flash : yellow border for 3 s on proactive_alert WS event.
  Lockdown    : pill background turns dark-red when fury stage = LOCKDOWN.

Thread model:
  Main thread → Qt event loop
  WS thread   → asyncio loop; FocusScoreEngine + PredictiveAlert run here
  Bridges     → QObject pyqtSignal — Qt delivers cross-thread via QueuedConnection
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

log = logging.getLogger("jarvis.overlay")

_STAGE_COLORS: dict[str, str] = {
    "GENTLE":    "#22c55e",
    "SARCASTIC": "#90ee90",
    "STERN":     "#fbbf24",
    "FURIOUS":   "#ff6b35",
    "LOCKDOWN":  "#ef4444",
}
_DEFAULT_COLOR  = "#22c55e"
_SUBJECT_COLOR  = "#38bdf8"   # sky-blue for subject bar
_ALERT_COLOR    = "#facc15"   # yellow for alert flash border


# ── Signal bridges (module-level singletons) ──────────────────────────────────

class _AngerBridge(QObject):
    updated = pyqtSignal(float, str, str)   # gauge, stage, hud_color

class _StudyBridge(QObject):
    updated = pyqtSignal(str, float, str)   # subject, pct, dday

class _GhostBridge(QObject):
    toggled = pyqtSignal(bool)              # is_ghost

class _AlertBridge(QObject):
    flashed = pyqtSignal(str)              # alert message (unused in UI, triggers flash)


_anger_bridge = _AngerBridge()
_study_bridge = _StudyBridge()
_ghost_bridge = _GhostBridge()
_alert_bridge = _AlertBridge()


# ── JarvisHUD composite widget ────────────────────────────────────────────────

class JarvisHUD(QWidget):
    """
    Single pill widget with fury gauge + subject progress + ghost mode.
    Dimensions: 240 × 118 px.
    """

    _W = 240
    _H = 118    # 56 fury + 6 gap + 56 subject

    def __init__(self) -> None:
        super().__init__(None)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self._W, self._H)

        # ── Widgets ────────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        # — Fury section —
        self._fury_label = QLabel("GENTLE  0%")
        self._fury_label.setStyleSheet(self._label_css(_DEFAULT_COLOR))
        layout.addWidget(self._fury_label)

        self._fury_bar = QProgressBar()
        self._fury_bar.setRange(0, 100)
        self._fury_bar.setValue(0)
        self._fury_bar.setTextVisible(False)
        self._fury_bar.setFixedHeight(7)
        self._fury_bar.setStyleSheet(self._bar_css(_DEFAULT_COLOR))
        layout.addWidget(self._fury_bar)

        # — Separator —
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,28);")
        layout.addWidget(sep)

        # — Subject section —
        self._subj_label = QLabel("— 과목 대기 중 —")
        self._subj_label.setStyleSheet(self._label_css(_SUBJECT_COLOR))
        layout.addWidget(self._subj_label)

        self._subj_bar = QProgressBar()
        self._subj_bar.setRange(0, 100)
        self._subj_bar.setValue(0)
        self._subj_bar.setTextVisible(False)
        self._subj_bar.setFixedHeight(7)
        self._subj_bar.setStyleSheet(self._bar_css(_SUBJECT_COLOR))
        layout.addWidget(self._subj_bar)

        # — D-Day chip —
        self._dday_label = QLabel("")
        self._dday_label.setStyleSheet(
            "color: rgba(255,255,255,120); font-size: 9px; "
            "font-family: monospace; letter-spacing: 1px;"
        )
        layout.addWidget(self._dday_label)

        # ── Internal state ────────────────────────────────────────────────────
        self._fury_color  = _DEFAULT_COLOR
        self._is_lockdown = False
        self._is_alerting = False

        # Ghost mode opacity animation
        self._ghost_target  = 0.88
        self._ghost_current = 0.88
        self.setWindowOpacity(self._ghost_current)

        self._ghost_timer = QTimer(self)
        self._ghost_timer.setInterval(16)   # ~60 fps ease
        self._ghost_timer.timeout.connect(self._step_ghost)

        # Alert flash: yellow border for 3 s
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.setInterval(3000)
        self._alert_timer.timeout.connect(self._end_alert)

        # ── Wire bridges ──────────────────────────────────────────────────────
        _anger_bridge.updated.connect(self._on_anger)
        _study_bridge.updated.connect(self._on_study)
        _ghost_bridge.toggled.connect(self._set_ghost)
        _alert_bridge.flashed.connect(self._on_alert)

    # ── Slot implementations ──────────────────────────────────────────────────

    def _on_anger(self, gauge: float, stage: str, color: str) -> None:
        self._fury_bar.setValue(int(gauge))
        self._fury_bar.setStyleSheet(self._bar_css(color))
        self._fury_label.setStyleSheet(self._label_css(color))
        self._fury_label.setText(f"{stage}  {gauge:.0f}%")
        self._fury_color  = color
        self._is_lockdown = (stage == "LOCKDOWN")
        self.update()

    def _on_study(self, subject: str, pct: float, dday: str) -> None:
        self._subj_bar.setValue(int(pct))
        self._subj_label.setText(f"{subject}  ·  {pct:.0f}%")
        self._dday_label.setText(dday)

    def _set_ghost(self, ghost: bool) -> None:
        """Activate/deactivate ghost mode — triggers opacity animation."""
        self._ghost_target = 0.28 if ghost else 0.88
        self._ghost_timer.start()

    def _step_ghost(self) -> None:
        diff = self._ghost_target - self._ghost_current
        if abs(diff) < 0.008:
            self._ghost_current = self._ghost_target
            self._ghost_timer.stop()
        else:
            self._ghost_current += diff * 0.12   # exponential ease
        self.setWindowOpacity(max(0.08, min(1.0, self._ghost_current)))

    def _on_alert(self, _message: str) -> None:
        self._is_alerting = True
        self.update()
        self._alert_timer.start()

    def _end_alert(self) -> None:
        self._is_alerting = False
        self.update()

    # ── Custom background painting ────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QColor(60, 0, 0, 200) if self._is_lockdown else QColor(0, 0, 0, 165)
        p.setBrush(bg)

        border = QColor(_ALERT_COLOR if self._is_alerting else self._fury_color)
        border.setAlpha(195)
        p.setPen(QPen(border, 1.5 if self._is_alerting else 1.0))

        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

    # ── Positioning ───────────────────────────────────────────────────────────

    def place_top_right(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        margin = 18
        self.move(screen.right() - self.width() - margin, screen.top() + margin)

    # ── CSS helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _bar_css(color: str) -> str:
        return (
            "QProgressBar { background: rgba(255,255,255,22); border-radius: 3px; border: none; }"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )

    @staticmethod
    def _label_css(color: str) -> str:
        return (
            f"color: {color}; "
            "font-family: 'Orbitron','Share Tech Mono',monospace; "
            "font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )


# ── WebSocket receive loop ────────────────────────────────────────────────────

async def _ws_receive_loop(ws_url: str, http_base: str) -> None:
    """
    Connects to the backend WS, receives events, and:
      • Routes anger_update → _anger_bridge (+ FocusScoreEngine)
      • Routes study_update → _study_bridge
      • Routes calendar_data → PredictiveAlert
      • Routes location_update → PredictiveAlert
      • Routes proactive_alert → _alert_bridge (flash)
    """
    from client.focus_score     import FocusScoreEngine
    from client.predictive_alert import PredictiveAlert
    import websockets

    focus_engine = FocusScoreEngine(
        on_ghost_mode=_ghost_bridge.toggled.emit,
        http_base=http_base,
    )
    predictor = PredictiveAlert(http_base=http_base)
    asyncio.create_task(predictor.run())

    backoff = 2.0
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                log.info("[Overlay] WS connected → %s", ws_url)
                await ws.send(json.dumps({"type": "register", "device": "overlay"}))
                backoff = 2.0

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    mtype = msg.get("type", "")

                    if mtype == "anger_update":
                        gauge = float(msg.get("gauge", 0.0))
                        stage = str(msg.get("stage", "GENTLE"))
                        color = str(msg.get("hud_color", _STAGE_COLORS.get(stage, _DEFAULT_COLOR)))
                        _anger_bridge.updated.emit(gauge, stage, color)
                        focus_engine.record_gauge(gauge)

                    elif mtype == "study_update":
                        data    = msg.get("data", {})
                        subject = str(data.get("subject", ""))
                        pct     = float(data.get("progress", 0.0))
                        dday    = str(data.get("dday", ""))
                        if subject:
                            _study_bridge.updated.emit(subject, pct, dday)

                    elif mtype == "calendar_data":
                        predictor.update_events(msg.get("events", []))

                    elif mtype == "location_update":
                        lat = float(msg.get("lat", 0.0))
                        lon = float(msg.get("lon", 0.0))
                        if lat or lon:
                            predictor.update_location(lat, lon)

                    elif mtype == "proactive_alert":
                        _alert_bridge.flashed.emit(str(msg.get("message", "")))

        except Exception as exc:
            log.warning("[Overlay] WS error: %s — retry in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _run_ws_thread(ws_url: str, http_base: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_ws_receive_loop(ws_url, http_base))


# ── Top-level orchestrator ────────────────────────────────────────────────────

class JarvisOverlay:
    """
    Creates QApplication, shows JarvisHUD, starts WS+engine thread.
    run() blocks until the Qt event loop exits.
    """

    def __init__(
        self,
        ws_url:    str = "ws://158.180.78.104:8000/ws",
        http_base: str = "http://158.180.78.104:8000",
    ) -> None:
        self._ws_url    = ws_url
        self._http_base = http_base

    def run(self) -> None:
        import sys
        app = QApplication.instance() or QApplication(sys.argv)

        hud = JarvisHUD()
        hud.place_top_right()
        hud.show()

        ws_thread = threading.Thread(
            target=_run_ws_thread,
            args=(self._ws_url, self._http_base),
            daemon=True,
            name="jarvis-ws-overlay",
        )
        ws_thread.start()

        log.info("[Overlay] Qt loop starting.")
        sys.exit(app.exec())
