"""
client/overlay.py — Transparent, click-through PyQt6 overlay.

Visual features:
  1. Particle Core (Arc Reactor) — QPropertyAnimation-driven rotating arcs;
     rotation speed scales with focus score (fast = high focus / alive,
     slow = low focus / sluggish).
  2. Glassmorphism — every paint grabs the pixels behind the widget,
     downscale-then-upscale blurs them, and overlays a frosted-glass tint.
  3. Data Pulse — proactive_alert fires 3 concentric ripple circles that
     expand from the pill center and fade out over 700 ms.

Sections (left → right in one pill widget):
  Left  : ArcReactorWidget (48 × 48 px, QPropertyAnimation)
  Right : Fury Gauge label + bar · separator · Subject label + bar · ctx chip

Modes:
  Ghost Mode  : focus score > 70 → overlay fades to 28 % opacity.
  Alert Flash : yellow border for 3 s on proactive_alert WS event
                + 3-wave ripple animation.
  Lockdown    : pill tint turns dark-red when fury stage = LOCKDOWN.

Thread model:
  Main thread → Qt event loop
  WS thread   → asyncio loop; FocusScoreEngine + PredictiveAlert run here
  Bridges     → QObject pyqtSignal (QueuedConnection, thread-safe)
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from PyQt6.QtCore import (
    QDateTime,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("jarvis.overlay")

_STAGE_COLORS: dict[str, str] = {
    "GENTLE":    "#22c55e",
    "SARCASTIC": "#90ee90",
    "STERN":     "#fbbf24",
    "FURIOUS":   "#ff6b35",
    "LOCKDOWN":  "#ef4444",
}
_DEFAULT_COLOR = "#22c55e"
_SUBJECT_COLOR = "#38bdf8"
_ALERT_COLOR   = "#facc15"


# ── Signal bridges (module-level singletons) ──────────────────────────────────

class _AngerBridge(QObject):
    updated = pyqtSignal(float, str, str)   # gauge, stage, hud_color

class _StudyBridge(QObject):
    updated = pyqtSignal(str, float, str)   # subject, pct, dday

class _GhostBridge(QObject):
    toggled = pyqtSignal(bool)

class _AlertBridge(QObject):
    flashed = pyqtSignal(str)               # alert message

class _MuteBridge(QObject):
    changed = pyqtSignal(bool, str, str)    # muted, icon, reason

class _FocusBridge(QObject):
    scored = pyqtSignal(float)              # 0-100 focus score → arc speed


_anger_bridge = _AngerBridge()
_study_bridge = _StudyBridge()
_ghost_bridge = _GhostBridge()
_alert_bridge = _AlertBridge()
_mute_bridge  = _MuteBridge()
_focus_bridge = _FocusBridge()


# ── Arc Reactor Widget ────────────────────────────────────────────────────────

class ArcReactorWidget(QWidget):
    """
    48 × 48 arc reactor icon.

    Paints three rotating arc segments (100° each, 120° apart) around a
    glowing core. QPropertyAnimation drives rotation_angle 0 → 360 in a
    continuous loop. Duration maps focus score → speed:
      score   0  →  3 000 ms / revolution  (sluggish)
      score 100  →    400 ms / revolution  (energised)
    """

    _SZ = 48

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SZ, self._SZ)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._angle = 0.0
        self._color = QColor(_DEFAULT_COLOR)

        self._anim = QPropertyAnimation(self, b"rotation_angle", self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(2000)
        self._anim.setLoopCount(-1)
        self._anim.start()

    # -- Qt property (required by QPropertyAnimation) -------------------------

    @pyqtProperty(float)
    def rotation_angle(self) -> float:
        return self._angle

    @rotation_angle.setter  # type: ignore[no-redef]
    def rotation_angle(self, val: float) -> None:
        self._angle = val % 360.0
        self.update()

    # -- Public API -----------------------------------------------------------

    def set_focus(self, score: float) -> None:
        """Adjust revolution speed from focus score (0-100)."""
        target_ms = int(3000 - score * 26)
        target_ms = max(400, min(3000, target_ms))
        if abs(self._anim.duration() - target_ms) <= 50:
            return
        pct = self._anim.currentTime() / max(self._anim.duration(), 1)
        self._anim.pause()
        self._anim.setDuration(target_ms)
        self._anim.setCurrentTime(int(pct * target_ms))
        self._anim.resume()

    def set_color(self, hex_color: str) -> None:
        self._color = QColor(hex_color)
        self.update()

    # -- Painting -------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = cy = self._SZ / 2.0

        # Outer guide ring (dim)
        outer_r = 20.0
        ring_pen = QPen(self._color.darker(200), 1.0)
        ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(ring_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), outer_r, outer_r)

        # Three rotating arcs
        arc_r = outer_r - 2.5
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        arc_pen = QPen(self._color, 2.5)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc_pen)
        for i in range(3):
            start = int((self._angle + i * 120.0) * 16)
            p.drawArc(arc_rect, start, int(100 * 16))

        # Mid ring (subtle depth layer)
        mid_r = 11.0
        mid_c = QColor(self._color)
        mid_c.setAlpha(70)
        p.setPen(QPen(mid_c, 1.0))
        p.drawEllipse(QPointF(cx, cy), mid_r, mid_r)

        # Core glow
        core_c = QColor(self._color)
        core_c.setAlpha(210)
        p.setBrush(core_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 6.5, 6.5)

        # Centre highlight
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)


# ── JarvisHUD composite widget ────────────────────────────────────────────────

class JarvisHUD(QWidget):
    """
    Pill: [ArcReactor] | [fury label + bar / sep / subject label + bar / ctx]
    Dimensions: 252 × 108 px.
    """

    _W = 252
    _H = 108

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

        # ── Layout ────────────────────────────────────────────────────────────
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 12, 10)
        root.setSpacing(10)

        self._reactor = ArcReactorWidget(self)
        root.addWidget(self._reactor, 0, Qt.AlignmentFlag.AlignVCenter)

        right = QVBoxLayout()
        right.setSpacing(4)

        self._fury_label = QLabel("GENTLE  0%")
        self._fury_label.setStyleSheet(self._label_css(_DEFAULT_COLOR))
        right.addWidget(self._fury_label)

        self._fury_bar = QProgressBar()
        self._fury_bar.setRange(0, 100)
        self._fury_bar.setValue(0)
        self._fury_bar.setTextVisible(False)
        self._fury_bar.setFixedHeight(6)
        self._fury_bar.setStyleSheet(self._bar_css(_DEFAULT_COLOR))
        right.addWidget(self._fury_bar)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,25);")
        right.addWidget(sep)

        self._subj_label = QLabel("— 과목 대기 중 —")
        self._subj_label.setStyleSheet(self._label_css(_SUBJECT_COLOR))
        right.addWidget(self._subj_label)

        self._subj_bar = QProgressBar()
        self._subj_bar.setRange(0, 100)
        self._subj_bar.setValue(0)
        self._subj_bar.setTextVisible(False)
        self._subj_bar.setFixedHeight(6)
        self._subj_bar.setStyleSheet(self._bar_css(_SUBJECT_COLOR))
        right.addWidget(self._subj_bar)

        self._ctx_label = QLabel("")
        self._ctx_label.setStyleSheet(
            "color: rgba(255,255,255,120); font-size: 9px; "
            "font-family: monospace; letter-spacing: 1px;"
        )
        right.addWidget(self._ctx_label)

        root.addLayout(right)

        # ── Internal state ────────────────────────────────────────────────────
        self._fury_color  = _DEFAULT_COLOR
        self._is_lockdown = False
        self._is_alerting = False
        self._muted       = False

        # Ghost opacity animation (~60 fps easing)
        self._ghost_target  = 0.88
        self._ghost_current = 0.88
        self.setWindowOpacity(self._ghost_current)
        self._ghost_timer = QTimer(self)
        self._ghost_timer.setInterval(16)
        self._ghost_timer.timeout.connect(self._step_ghost)

        # Alert flash border
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.setInterval(3000)
        self._alert_timer.timeout.connect(self._end_alert)

        # Data Pulse ripple state — list of {start_ms, color}
        self._ripples: list[dict] = []
        self._ripple_timer = QTimer(self)
        self._ripple_timer.setInterval(33)          # ~30 fps
        self._ripple_timer.timeout.connect(self.update)

        # ── Bridge connections ────────────────────────────────────────────────
        _anger_bridge.updated.connect(self._on_anger)
        _study_bridge.updated.connect(self._on_study)
        _ghost_bridge.toggled.connect(self._set_ghost)
        _alert_bridge.flashed.connect(self._on_alert)
        _mute_bridge.changed.connect(self._on_mute)
        _focus_bridge.scored.connect(self._reactor.set_focus)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_anger(self, gauge: float, stage: str, color: str) -> None:
        self._fury_bar.setValue(int(gauge))
        self._fury_bar.setStyleSheet(self._bar_css(color))
        self._fury_label.setStyleSheet(self._label_css(color))
        self._fury_label.setText(f"{stage}  {gauge:.0f}%")
        self._fury_color  = color
        self._is_lockdown = (stage == "LOCKDOWN")
        self._reactor.set_color(color)
        self.update()

    def _on_study(self, subject: str, pct: float, dday: str) -> None:
        self._subj_bar.setValue(int(pct))
        self._subj_label.setText(f"{subject}  ·  {pct:.0f}%")
        if not self._muted:
            self._ctx_label.setText(dday)

    def _on_mute(self, muted: bool, icon: str, reason: str) -> None:
        self._muted = muted
        if muted:
            self._ctx_label.setStyleSheet(
                "color: #fbbf24; font-size: 9px; font-family: monospace; letter-spacing: 1px;"
            )
            self._ctx_label.setText(f"{icon}  {reason}")
        else:
            self._ctx_label.setStyleSheet(
                "color: rgba(255,255,255,120); font-size: 9px; "
                "font-family: monospace; letter-spacing: 1px;"
            )
            self._ctx_label.setText("")

    def _set_ghost(self, ghost: bool) -> None:
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
        self._add_ripple(_ALERT_COLOR)
        QTimer.singleShot(160, lambda: self._add_ripple(_ALERT_COLOR))
        QTimer.singleShot(320, lambda: self._add_ripple(_ALERT_COLOR))
        self._alert_timer.start()

    def _add_ripple(self, color: str) -> None:
        self._ripples.append({
            "start_ms": QDateTime.currentMSecsSinceEpoch(),
            "color": color,
        })
        if not self._ripple_timer.isActive():
            self._ripple_timer.start()

    def _end_alert(self) -> None:
        self._is_alerting = False
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Rounded clip region (all drawing inside the pill)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 12, 12)
        p.setClipPath(clip)

        # ── 1. Glassmorphism: blurred screen capture ───────────────────────────
        self._draw_glass_bg(p)

        # ── 2. Data Pulse ripples ─────────────────────────────────────────────
        self._draw_ripples(p)

        # ── 3. Border ─────────────────────────────────────────────────────────
        p.setClipping(False)
        border_c = QColor(_ALERT_COLOR if self._is_alerting else self._fury_color)
        border_c.setAlpha(200)
        p.setPen(QPen(border_c, 1.5 if self._is_alerting else 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)

    def _draw_glass_bg(self, p: QPainter) -> None:
        """
        Capture what is visually behind the widget, blur via downscale trick,
        then overlay a frosted-glass tint.  Falls back gracefully if the screen
        grab is unavailable (Wayland / permission denied).
        """
        try:
            pos = self.mapToGlobal(self.rect().topLeft())
            raw = QApplication.primaryScreen().grabWindow(
                0, pos.x(), pos.y(), self.width(), self.height()
            )
            if not raw.isNull():
                # Quick approximate blur: 4× downscale → upscale
                small = raw.scaled(
                    max(1, self.width() // 4),
                    max(1, self.height() // 4),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                blurred = small.scaled(
                    self.width(), self.height(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p.drawPixmap(0, 0, blurred)
        except Exception:
            pass  # transparent fallback (WA_TranslucentBackground)

        # Frosted tint layer
        tint = QColor(60, 0, 0, 175) if self._is_lockdown else QColor(4, 8, 20, 165)
        p.fillRect(self.rect(), tint)

        # Top-edge sheen line (glass specular highlight)
        p.fillRect(0, 0, self.width(), 1, QColor(255, 255, 255, 28))

    def _draw_ripples(self, p: QPainter) -> None:
        """Expand concentric circles from pill centre; prune expired ripples."""
        now_ms = QDateTime.currentMSecsSinceEpoch()
        cx, cy = self.width() / 2.0, self.height() / 2.0
        max_r  = max(self.width(), self.height()) * 0.78
        duration_ms = 700
        alive: list[dict] = []
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rip in self._ripples:
            elapsed = now_ms - rip["start_ms"]
            if elapsed >= duration_ms:
                continue
            t = elapsed / duration_ms           # 0 → 1
            r = t * max_r
            alpha = int(145 * (1.0 - t))
            c = QColor(rip["color"])
            c.setAlpha(alpha)
            p.setPen(QPen(c, 1.5))
            p.drawEllipse(QPointF(cx, cy), r, r)
            alive.append(rip)
        self._ripples = alive
        if not self._ripples:
            self._ripple_timer.stop()

    # ── Positioning ───────────────────────────────────────────────────────────

    def place_top_right(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.right() - self.width() - 18, screen.top() + 18)

    # ── CSS helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _bar_css(color: str) -> str:
        return (
            "QProgressBar { background: rgba(255,255,255,18); border-radius: 3px; border: none; }"
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
    from client.focus_score      import FocusScoreEngine
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
                        # Feed live focus score to arc reactor speed
                        _focus_bridge.scored.emit(focus_engine.current_score)

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

                    elif mtype == "stealth_update":
                        muted  = bool(msg.get("muted", False))
                        icon   = str(msg.get("icon", ""))
                        reason = str(msg.get("reason", ""))
                        _mute_bridge.changed.emit(muted, icon, reason)

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
