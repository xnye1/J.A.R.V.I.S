"""
client/overlay.py — J.A.R.V.I.S. v2.5  Awakening HUD

Full-screen transparent overlay with Bento-Box grid layout:

  ┌─────────────────┬──────────────────────────────┬──────────────────┐
  │                 │                              │                  │
  │  LogStream      │       AwakeningCore           │  BioRhythm       │
  │  (log stream)   │   (arc reactor + identity)   │   (4 gauges)     │
  │                 │                              │                  │
  │                 ├──────────────────────────────┴──────────────────┤
  │                 │          FocusHistoryGraph                      │
  └─────────────────┴─────────────────────────────────────────────────┘

Boot sequence (≈ 3.2 s total):
  0 ms      : BootScreen covers everything — tiny reactor point
  0–650 ms  : Arc reactor scales up  (QPropertyAnimation, OutCubic)
  650–1 100 ms : "INITIATING..." + "USER IDENTIFIED: Sir"  (type-writer)
  1 100 ms  : Grid panels fade in (sequential, 400 ms each)
  1 800 ms  : "Welcome home, Sir. System fully operational."
  2 800 ms  : BootScreen fades out
  3 200 ms  : BootScreen hidden — HUD fully operational

Performance notes:
  - No screen capture in paintEvent (no grabWindow per-frame).
  - WA_TranslucentBackground: OS compositor handles real transparency.
  - Panel updates are data-driven (update() only on new WS data).
  - ArcReactorCore uses QPropertyAnimation (no manual timer).
  - FocusHistoryGraph and LogStream have independent 33 ms repaint timers.
  - Ghost mode: setWindowOpacity on the top-level window (whole HUD dims).

Thread model: same as before — Qt main thread + asyncio WS thread, bridged
via pyqtSignal (QueuedConnection guarantees thread-safety).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import threading
import time
from collections import deque

from PyQt6.QtCore import (
    QDateTime,
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QApplication, QGridLayout, QWidget

log = logging.getLogger("jarvis.overlay")

# ── Palette ───────────────────────────────────────────────────────────────────

_C_BG        = QColor(4,   8,  22, 188)   # card fill
_C_BORDER    = QColor(0,  175, 255,  85)   # card border
_C_CYAN      = "#00b4ff"
_C_GREEN     = "#22c55e"
_C_LIME      = "#4ade80"
_C_YELLOW    = "#fbbf24"
_C_ORANGE    = "#ff6b35"
_C_RED       = "#ef4444"
_C_DIM       = QColor(140, 160, 200,  90)  # dim label text

_STAGE_COLORS: dict[str, str] = {
    "GENTLE":    _C_GREEN,
    "SARCASTIC": _C_LIME,
    "STERN":     _C_YELLOW,
    "FURIOUS":   _C_ORANGE,
    "LOCKDOWN":  _C_RED,
}
_DEFAULT_COLOR = _C_GREEN

# ── Signal bridges ────────────────────────────────────────────────────────────

class _AngerBridge(QObject):
    updated = pyqtSignal(float, str, str)     # gauge, stage, color

class _StudyBridge(QObject):
    updated = pyqtSignal(str, float, str)     # subject, pct, dday

class _GhostBridge(QObject):
    toggled = pyqtSignal(bool)

class _AlertBridge(QObject):
    flashed = pyqtSignal(str)

class _MuteBridge(QObject):
    changed = pyqtSignal(bool, str, str)

class _FocusBridge(QObject):
    scored = pyqtSignal(float)                # 0-100

class _SysBridge(QObject):
    updated = pyqtSignal(float, float, float) # cpu%, mem%, disk%

class _LogBridge(QObject):
    line = pyqtSignal(str, bool)             # text, is_important


_anger_bridge = _AngerBridge()
_study_bridge = _StudyBridge()
_ghost_bridge = _GhostBridge()
_alert_bridge = _AlertBridge()
_mute_bridge  = _MuteBridge()
_focus_bridge = _FocusBridge()
_sys_bridge   = _SysBridge()
_log_bridge   = _LogBridge()

# Shared system state (asyncio thread writes, Qt thread reads)
_sys_state: dict = {"cpu": 0.0, "mem": 0.0, "disk": 0.0, "focus": 50.0}

# ── Neon glow helper ──────────────────────────────────────────────────────────

_NEON_ARC   = [(12, 14), (7, 38), (4, 95), (2, 220)]   # (width, alpha)
_NEON_THIN  = [(7,  14), (4, 40), (2, 90), (1, 200)]

def _neon_arc(
    p: QPainter,
    rect: QRectF,
    start_deg: float,
    span_deg: float,
    color: str | QColor,
    layers: list = _NEON_ARC,
) -> None:
    """Draw an arc with neon-glow multi-pass."""
    base = QColor(color)
    for width, alpha in layers:
        c = QColor(base)
        c.setAlpha(alpha)
        pen = QPen(c, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))


def _neon_ellipse(
    p: QPainter,
    center: QPointF,
    rx: float,
    ry: float,
    color: str | QColor,
    layers: list = _NEON_THIN,
) -> None:
    """Draw an ellipse (circle) with neon glow."""
    base = QColor(color)
    for width, alpha in layers:
        c = QColor(base)
        c.setAlpha(alpha)
        p.setPen(QPen(c, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, rx + width / 2, ry + width / 2)


# ── GlassPanel base class ─────────────────────────────────────────────────────

class GlassPanel(QWidget):
    """
    Base for all HUD panels.
    Provides:
      - `panel_opacity` pyqtProperty (0→1) for boot-reveal animation
      - `_paint_bg()`: frosted glass card background
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._panel_opacity: float = 0.0

    @pyqtProperty(float)
    def panel_opacity(self) -> float:
        return self._panel_opacity

    @panel_opacity.setter  # type: ignore[no-redef]
    def panel_opacity(self, val: float) -> None:
        self._panel_opacity = max(0.0, min(1.0, val))
        self.update()

    def _paint_bg(self, p: QPainter, accent: QColor | None = None) -> None:
        """Fill glass background; caller must have set opacity beforehand."""
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        p.fillRect(self.rect(), _C_BG)
        # Top sheen
        p.fillRect(0, 0, self.width(), 1, QColor(255, 255, 255, 28))
        p.setClipping(False)
        border = accent if accent else _C_BORDER
        p.setPen(QPen(border, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(clip)


# ── ArcReactorCore ────────────────────────────────────────────────────────────

class ArcReactorCore(GlassPanel):
    """
    Center-top panel: spinning arc reactor + identity line.
    Reactor size = min(w, h) × 0.48. Focus score drives rotation speed.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle:    float = 0.0
        self._color:    str   = _DEFAULT_COLOR
        self._focus:    float = 50.0
        self._stage:    str   = "GENTLE"
        self._fury_pct: float = 0.0

        self._spin_anim = QPropertyAnimation(self, b"rotation_angle", self)
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(360.0)
        self._spin_anim.setDuration(1800)
        self._spin_anim.setLoopCount(-1)
        self._spin_anim.start()

    # ── Qt properties ─────────────────────────────────────────────────────────

    @pyqtProperty(float)
    def rotation_angle(self) -> float:
        return self._angle

    @rotation_angle.setter  # type: ignore[no-redef]
    def rotation_angle(self, v: float) -> None:
        self._angle = v % 360.0
        self.update()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_focus(self, score: float) -> None:
        self._focus = score
        target = int(3000 - score * 26)
        target = max(350, min(3000, target))
        if abs(self._spin_anim.duration() - target) > 60:
            pct = self._spin_anim.currentTime() / max(self._spin_anim.duration(), 1)
            self._spin_anim.pause()
            self._spin_anim.setDuration(target)
            self._spin_anim.setCurrentTime(int(pct * target))
            self._spin_anim.resume()

    def set_anger(self, gauge: float, stage: str, color: str) -> None:
        self._fury_pct = gauge
        self._stage    = stage
        self._color    = color
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._panel_opacity)
        self._paint_bg(p)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0 - 18

        r_outer = min(w, h) * 0.23
        self._draw_reactor(p, cx, cy, r_outer)

        # Identity text
        p.setOpacity(self._panel_opacity * 0.9)
        title_font = QFont("Orbitron, Share Tech Mono, monospace", 13, QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(QColor(_C_CYAN))
        p.drawText(
            QRectF(0, cy + r_outer + 18, w, 28),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "J.A.R.V.I.S.",
        )

        sub_font = QFont("Share Tech Mono, monospace", 9)
        p.setFont(sub_font)
        p.setPen(_C_DIM)
        p.drawText(
            QRectF(0, cy + r_outer + 46, w, 20),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            f"{self._stage}  ·  focus {self._focus:.0f}%",
        )

    def _draw_reactor(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        color = self._color

        # Outer orbit ring
        _neon_ellipse(p, QPointF(cx, cy), r, r, color, _NEON_THIN)

        # Three rotating neon arcs (120° apart)
        arc_r   = r - 4
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        for i in range(3):
            _neon_arc(p, arc_rect, self._angle + i * 120.0, 95.0, color)

        # Mid ring
        mid_r = r * 0.52
        _neon_ellipse(p, QPointF(cx, cy), mid_r, mid_r, color, _NEON_THIN)

        # Core glow (filled)
        core_r = r * 0.26
        core_c = QColor(color)
        core_c.setAlpha(200)
        p.setBrush(core_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # Centre highlight
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(cx, cy), core_r * 0.35, core_r * 0.35)

        # Fury arc (background track + value)
        fury_r    = r + 14
        fury_rect = QRectF(cx - fury_r, cy - fury_r, fury_r * 2, fury_r * 2)
        p.setPen(QPen(QColor(255, 255, 255, 18), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(fury_rect, int(225 * 16), int(-270 * 16))
        span = -int(self._fury_pct / 100.0 * 270 * 16)
        if span:
            _neon_arc(p, fury_rect, 225.0, -self._fury_pct / 100.0 * 270, color,
                      [(6, 20), (3, 60), (2, 180)])


# ── LogStreamWidget ───────────────────────────────────────────────────────────

_LOG_ATMOSPHERICS = [
    "THERMAL_CTRL  nominal",
    "SEC_SCAN  perimeter clear",
    "QUANTUM_PROC  queue nominal",
    "SYNC_PROTO  heartbeat ✓",
    "DORMANCY_CHECK  nominal",
    "DEPLOY_MGR  last push clean",
    "CIPHER_LAYER  integrity ok",
    "NET_WATCH  latency nominal",
    "SUBSYS_CHECK  all green",
    "POWER_CELL  stable",
]

class LogStreamWidget(GlassPanel):
    """Scrolling atmospheric system log — left panel."""

    _CAPACITY = 32

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: deque[tuple[int, str, bool]] = deque(maxlen=self._CAPACITY)
        # (timestamp_ms, text, is_important)

        self._atmos_timer = QTimer(self)
        self._atmos_timer.setInterval(2200)
        self._atmos_timer.timeout.connect(self._gen_atmospheric)
        self._atmos_timer.start()

        _log_bridge.line.connect(self._append)

    def _append(self, text: str, important: bool) -> None:
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._lines.append((QDateTime.currentMSecsSinceEpoch(), f"[{ts}] {text}", important))
        self.update()

    def _gen_atmospheric(self) -> None:
        s  = _sys_state
        tpl = random.choice(_LOG_ATMOSPHERICS)
        self._append(tpl, False)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._panel_opacity)
        self._paint_bg(p)

        if not self._lines:
            return

        pad   = 12
        line_h = 16
        font  = QFont("Share Tech Mono, Consolas, monospace", 8)
        p.setFont(font)

        # Draw title
        p.setPen(QColor(_C_CYAN))
        p.setFont(QFont("Orbitron, monospace", 8, QFont.Weight.Bold))
        p.drawText(pad, 20, "SYS LOG")
        p.setFont(font)

        now_ms   = QDateTime.currentMSecsSinceEpoch()
        y_start  = 36
        avail_h  = self.height() - y_start - pad
        max_vis  = max(1, avail_h // line_h)
        visible  = list(self._lines)[-max_vis:]

        for i, (ts_ms, text, important) in enumerate(visible):
            age_s   = (now_ms - ts_ms) / 1000.0
            age_alpha = max(60, int(220 - age_s * 4))
            if important:
                c = QColor(_C_YELLOW)
                c.setAlpha(min(220, age_alpha + 40))
            else:
                c = QColor(_C_GREEN if i == len(visible) - 1 else _C_CYAN)
                c.setAlpha(age_alpha)
            p.setPen(c)
            y = y_start + i * line_h
            p.drawText(
                QRectF(pad, y, self.width() - pad * 2, line_h),
                Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        # Cursor blink on last line
        if int(time.time() * 2) % 2 == 0:
            p.setPen(QColor(_C_GREEN))
            p.drawText(pad, y_start + len(visible) * line_h, "▌")


# ── BioRhythmCard ─────────────────────────────────────────────────────────────

class BioRhythmCard(GlassPanel):
    """Right-top panel: four neon circular gauges."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values = {"FOCUS": 50.0, "FURY": 0.0, "CPU": 0.0, "MEM": 0.0}
        self._colors = {
            "FOCUS": _C_CYAN,
            "FURY":  _C_ORANGE,
            "CPU":   _C_GREEN,
            "MEM":   _C_YELLOW,
        }

    def set_focus(self, v: float) -> None:
        self._values["FOCUS"] = v;  self.update()

    def set_anger(self, v: float) -> None:
        self._values["FURY"] = v;   self.update()

    def set_sys(self, cpu: float, mem: float, _disk: float) -> None:
        self._values["CPU"] = cpu
        self._values["MEM"] = mem
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._panel_opacity)
        self._paint_bg(p)

        # Title
        p.setPen(QColor(_C_CYAN))
        p.setFont(QFont("Orbitron, monospace", 8, QFont.Weight.Bold))
        p.drawText(14, 20, "BIO RHYTHM")

        w, h = self.width(), self.height()
        pad  = 14
        title_h = 30
        cols = 2
        rows = 2
        gap  = 10
        avail_w = w - pad * 2 - gap * (cols - 1)
        avail_h = h - title_h - pad - gap * (rows - 1)
        cell_w  = avail_w // cols
        cell_h  = avail_h // rows
        g_sz    = min(cell_w, cell_h) - 20

        keys = list(self._values.keys())
        for idx, key in enumerate(keys):
            col = idx % cols
            row = idx // cols
            cx  = pad + col * (cell_w + gap) + cell_w // 2
            cy  = title_h + row * (cell_h + gap) + cell_h // 2
            self._draw_gauge(p, cx, cy, g_sz // 2, key,
                             self._values[key], self._colors[key])

    def _draw_gauge(
        self, p: QPainter, cx: int, cy: int, r: int,
        label: str, value: float, color: str,
    ) -> None:
        # Background track
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        p.setPen(QPen(QColor(255, 255, 255, 22), 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(225 * 16), int(-270 * 16))

        # Value arc
        if value > 0:
            span = -value / 100.0 * 270
            _neon_arc(p, rect, 225.0, span, color)

        # Center value text
        p.setPen(QColor(color))
        p.setFont(QFont("Orbitron, monospace", int(r * 0.38), QFont.Weight.Bold))
        p.drawText(
            QRectF(cx - r, cy - r * 0.5, r * 2, r),
            Qt.AlignmentFlag.AlignCenter,
            f"{value:.0f}",
        )

        # Label
        p.setFont(QFont("Share Tech Mono, monospace", max(7, int(r * 0.22))))
        p.setPen(_C_DIM)
        p.drawText(
            QRectF(cx - r, cy + r * 0.45, r * 2, r * 0.4),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )


# ── FocusHistoryGraph ─────────────────────────────────────────────────────────

class FocusHistoryGraph(GlassPanel):
    """Bottom-center+right panel: scrolling EWMA focus score line chart."""

    _CAPACITY = 120   # ~20 min at 10-s intervals

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: deque[float] = deque(maxlen=self._CAPACITY)

        # Seed with neutral values so graph isn't empty on boot
        for _ in range(20):
            self._history.append(50.0 + random.uniform(-3, 3))

        # Gentle repaint timer so cursor/line animates even without new data
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self.update)
        self._tick.start()

    def push(self, score: float) -> None:
        self._history.append(score)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._panel_opacity)
        self._paint_bg(p)

        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 48, 18, 30, 22

        # Title
        p.setPen(QColor(_C_CYAN))
        p.setFont(QFont("Orbitron, monospace", 8, QFont.Weight.Bold))
        p.drawText(pad_l, 20, "FOCUS HISTORY  (EWMA)")

        gw = w - pad_l - pad_r
        gh = h - pad_t - pad_b

        # Grid
        p.setPen(QPen(QColor(0, 180, 255, 22), 1))
        for pct in (25, 50, 75, 100):
            y = pad_t + gh - gh * pct / 100
            p.drawLine(pad_l, int(y), pad_l + gw, int(y))

        # Threshold lines
        def _thresh(v: float, color: str, label: str) -> None:
            y = int(pad_t + gh - gh * v / 100)
            pen = QPen(QColor(color), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(pad_l, y, pad_l + gw, y)
            p.setFont(QFont("Share Tech Mono, monospace", 7))
            p.setPen(QColor(color))
            p.drawText(2, y + 4, label)

        _thresh(70.0, _C_CYAN,   "GHOST")
        _thresh(60.0, _C_YELLOW, "BREAK")

        # Y axis labels
        p.setFont(QFont("Share Tech Mono, monospace", 7))
        p.setPen(_C_DIM)
        for pct in (0, 25, 50, 75, 100):
            y = int(pad_t + gh - gh * pct / 100)
            p.drawText(1, y + 4, str(pct))

        if len(self._history) < 2:
            return

        points = list(self._history)
        n      = len(points)

        def _to_xy(i: int, v: float):
            x = pad_l + gw * i / (n - 1)
            y = pad_t + gh - gh * max(0.0, min(100.0, v)) / 100.0
            return x, y

        # Area fill (gradient below line)
        fill = QPainterPath()
        x0, y0 = _to_xy(0, points[0])
        fill.moveTo(x0, pad_t + gh)
        fill.lineTo(x0, y0)
        for i, v in enumerate(points[1:], 1):
            xi, yi = _to_xy(i, v)
            fill.lineTo(xi, yi)
        fill.lineTo(_to_xy(n - 1, points[-1])[0], pad_t + gh)
        fill.closeSubpath()

        grad = QLinearGradient(0, pad_t, 0, pad_t + gh)
        grad.setColorAt(0.0, QColor(0, 200, 100, 55))
        grad.setColorAt(1.0, QColor(0, 200, 100,  0))
        p.fillPath(fill, grad)

        # Line
        line_path = QPainterPath()
        x0, y0 = _to_xy(0, points[0])
        line_path.moveTo(x0, y0)
        for i, v in enumerate(points[1:], 1):
            line_path.lineTo(*_to_xy(i, v))

        for width, alpha in [(5, 18), (3, 55), (1.5, 210)]:
            c = QColor(_C_GREEN)
            c.setAlpha(alpha)
            p.setPen(QPen(c, width))
            p.drawPath(line_path)

        # Current value dot
        lx, ly = _to_xy(n - 1, points[-1])
        p.setBrush(QColor(_C_GREEN))
        p.setPen(QPen(QColor(255, 255, 255, 180), 1))
        p.drawEllipse(QPointF(lx, ly), 4.0, 4.0)

        # Current value label
        p.setFont(QFont("Orbitron, monospace", 9, QFont.Weight.Bold))
        p.setPen(QColor(_C_GREEN))
        p.drawText(int(lx) + 8, int(ly) + 4, f"{points[-1]:.0f}")


# ── BootScreen ────────────────────────────────────────────────────────────────

class BootScreen(QWidget):
    """
    Full-size child overlay that plays the Awakening boot sequence.
    Emits panels_reveal at ~1 100 ms and boot_done at ~3 200 ms.
    """

    panels_reveal = pyqtSignal()
    boot_done     = pyqtSignal()

    # Phases
    _PH_IDLE    = 0
    _PH_REACTOR = 1
    _PH_SCAN    = 2
    _PH_WELCOME = 3
    _PH_FADEOUT = 4

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(parent.rect())

        self._phase        = self._PH_IDLE
        self._reactor_sc   = 0.0
        self._fade_op      = 1.0
        self._scan_lines   = ["", ""]         # two typed lines
        self._full_scan    = [
            "INITIATING NEURAL NETWORK...",
            "USER IDENTIFIED:  Sir",
        ]
        self._scan_idx     = [0, 0]           # chars revealed per line
        self._welcome_op   = 0.0
        self._angle        = 0.0

        # Spin timer (independent from main reactor)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._tick_spin)

        # Reactor scale animation
        self._sc_anim = QPropertyAnimation(self, b"reactor_scale", self)
        self._sc_anim.setStartValue(0.0)
        self._sc_anim.setEndValue(1.0)
        self._sc_anim.setDuration(650)
        self._sc_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Fade-out animation
        self._fade_anim = QPropertyAnimation(self, b"fade_opacity", self)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setDuration(500)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self._fade_anim.finished.connect(self._on_fade_done)

        # Typing timers
        self._type_timers = [QTimer(self), QTimer(self)]
        self._type_timers[0].setInterval(55)
        self._type_timers[1].setInterval(55)
        self._type_timers[0].timeout.connect(lambda: self._type(0))
        self._type_timers[1].timeout.connect(lambda: self._type(1))

        # Welcome fade
        self._welcome_timer = QTimer(self)
        self._welcome_timer.setInterval(16)
        self._welcome_timer.timeout.connect(self._tick_welcome)

    # ── Qt properties ─────────────────────────────────────────────────────────

    @pyqtProperty(float)
    def reactor_scale(self) -> float:
        return self._reactor_sc

    @reactor_scale.setter  # type: ignore[no-redef]
    def reactor_scale(self, v: float) -> None:
        self._reactor_sc = v
        self.update()

    @pyqtProperty(float)
    def fade_opacity(self) -> float:
        return self._fade_op

    @fade_opacity.setter  # type: ignore[no-redef]
    def fade_opacity(self, v: float) -> None:
        self._fade_op = v
        self.update()

    # ── Sequence control ──────────────────────────────────────────────────────

    def start(self) -> None:
        self._phase = self._PH_REACTOR
        self.show()
        self.raise_()
        self._spin_timer.start()
        self._sc_anim.start()
        QTimer.singleShot(680,  self._begin_scan_line0)
        QTimer.singleShot(1050, self._begin_scan_line1)
        QTimer.singleShot(1150, self.panels_reveal.emit)
        QTimer.singleShot(1600, self._begin_welcome)
        QTimer.singleShot(2800, self._begin_fadeout)

    def _begin_scan_line0(self) -> None:
        self._phase = self._PH_SCAN
        self._type_timers[0].start()

    def _begin_scan_line1(self) -> None:
        self._type_timers[1].start()

    def _begin_welcome(self) -> None:
        self._phase = self._PH_WELCOME
        self._welcome_timer.start()

    def _begin_fadeout(self) -> None:
        self._phase = self._PH_FADEOUT
        self._fade_anim.start()

    def _on_fade_done(self) -> None:
        self._spin_timer.stop()
        self._welcome_timer.stop()
        self.hide()
        self.boot_done.emit()

    def _tick_spin(self) -> None:
        self._angle = (self._angle + 3.0) % 360.0
        self.update()

    def _type(self, line_idx: int) -> None:
        si = self._scan_idx[line_idx]
        full = self._full_scan[line_idx]
        if si < len(full):
            self._scan_lines[line_idx] = full[: si + 1]
            self._scan_idx[line_idx]   += 1
            self.update()
        else:
            self._type_timers[line_idx].stop()

    def _tick_welcome(self) -> None:
        self._welcome_op = min(1.0, self._welcome_op + 0.04)
        self.update()
        if self._welcome_op >= 1.0:
            self._welcome_timer.stop()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._fade_op)

        # Dark backdrop
        p.fillRect(self.rect(), QColor(2, 5, 15, 235))

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0 - 60

        # Arc reactor
        r = min(w, h) * 0.18 * self._reactor_sc
        if r > 2:
            self._draw_boot_reactor(p, cx, cy, r)

        # Scan text
        if self._phase in (self._PH_SCAN, self._PH_WELCOME, self._PH_FADEOUT):
            scan_font = QFont("Share Tech Mono, Consolas, monospace", 13)
            p.setFont(scan_font)
            for i, txt in enumerate(self._scan_lines):
                if txt:
                    c = QColor(_C_CYAN if i == 0 else _C_GREEN)
                    p.setPen(c)
                    p.drawText(
                        QRectF(0, cy + r + 30 + i * 28, w, 28),
                        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                        txt + ("▌" if self._type_timers[i].isActive() and
                               int(time.time() * 4) % 2 == 0 else ""),
                    )

        # Welcome message
        if self._welcome_op > 0.01:
            p.setOpacity(self._fade_op * self._welcome_op)
            wf = QFont("Orbitron, monospace", 18, QFont.Weight.Bold)
            p.setFont(wf)
            p.setPen(QColor(_C_CYAN))
            p.drawText(
                QRectF(0, h / 2.0 + 80, w, 44),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                "Welcome home, Sir.",
            )
            sf = QFont("Share Tech Mono, monospace", 11)
            p.setFont(sf)
            p.setPen(QColor(140, 200, 255, 200))
            p.drawText(
                QRectF(0, h / 2.0 + 124, w, 28),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                "System fully operational.",
            )

    def _draw_boot_reactor(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        _neon_ellipse(p, QPointF(cx, cy), r, r, _C_CYAN, _NEON_THIN)

        arc_r    = r - 4
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        for i in range(3):
            _neon_arc(p, arc_rect, self._angle + i * 120.0, 95.0, _C_CYAN)

        _neon_ellipse(p, QPointF(cx, cy), r * 0.52, r * 0.52, _C_CYAN, _NEON_THIN)

        core_r = r * 0.28
        core_c = QColor(_C_CYAN)
        core_c.setAlpha(200)
        p.setBrush(core_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(cx, cy), core_r * 0.35, core_r * 0.35)


# ── JarvisFullHUD ─────────────────────────────────────────────────────────────

class JarvisFullHUD(QWidget):
    """
    Full-screen transparent overlay. Contains Bento grid + BootScreen.
    Ghost mode: setWindowOpacity on this widget dims the entire HUD.
    """

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

        # ── Grid ──────────────────────────────────────────────────────────────
        grid = QGridLayout(self)
        grid.setContentsMargins(20, 20, 20, 20)
        grid.setSpacing(14)
        grid.setColumnStretch(0, 20)
        grid.setColumnStretch(1, 46)
        grid.setColumnStretch(2, 34)
        grid.setRowStretch(0, 62)
        grid.setRowStretch(1, 38)

        self._log   = LogStreamWidget(self)
        self._core  = ArcReactorCore(self)
        self._bio   = BioRhythmCard(self)
        self._graph = FocusHistoryGraph(self)

        grid.addWidget(self._log,   0, 0, 2, 1)   # full left column
        grid.addWidget(self._core,  0, 1, 1, 1)
        grid.addWidget(self._bio,   0, 2, 1, 1)
        grid.addWidget(self._graph, 1, 1, 1, 2)   # bottom, spans col 1-2

        # ── Ghost mode ────────────────────────────────────────────────────────
        self._ghost_target  = 0.88
        self._ghost_current = 0.88
        self.setWindowOpacity(self._ghost_current)
        self._ghost_timer = QTimer(self)
        self._ghost_timer.setInterval(16)
        self._ghost_timer.timeout.connect(self._step_ghost)

        # Ripple state (full-screen)
        self._ripples: list[dict] = []
        self._ripple_timer = QTimer(self)
        self._ripple_timer.setInterval(33)
        self._ripple_timer.timeout.connect(self.update)

        # ── Boot screen ───────────────────────────────────────────────────────
        self._boot = BootScreen(self)
        self._boot.panels_reveal.connect(self._reveal_panels)
        self._boot.boot_done.connect(self._on_boot_done)
        self._panel_anims: list[QPropertyAnimation] = []

        # ── Wire bridges ──────────────────────────────────────────────────────
        _anger_bridge.updated.connect(self._on_anger)
        _focus_bridge.scored.connect(self._on_focus)
        _sys_bridge.updated.connect(self._on_sys)
        _alert_bridge.flashed.connect(self._on_alert)
        _ghost_bridge.toggled.connect(self._set_ghost)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_anger(self, gauge: float, stage: str, color: str) -> None:
        self._core.set_anger(gauge, stage, color)
        self._bio.set_anger(gauge)

    def _on_focus(self, score: float) -> None:
        self._core.set_focus(score)
        self._bio.set_focus(score)
        self._graph.push(score)

    def _on_sys(self, cpu: float, mem: float, disk: float) -> None:
        self._bio.set_sys(cpu, mem, disk)
        _log_bridge.line.emit(
            f"CPU {cpu:.0f}%  MEM {mem:.0f}%  DISK {disk:.0f}%", False
        )

    def _on_alert(self, _msg: str) -> None:
        self._ripples.append({
            "start_ms": QDateTime.currentMSecsSinceEpoch(),
            "color": _C_YELLOW,
        })
        QTimer.singleShot(200, lambda: self._ripples.append({
            "start_ms": QDateTime.currentMSecsSinceEpoch(),
            "color": _C_YELLOW,
        }))
        if not self._ripple_timer.isActive():
            self._ripple_timer.start()
        self.update()

    def _set_ghost(self, ghost: bool) -> None:
        self._ghost_target = 0.12 if ghost else 0.88
        self._ghost_timer.start()

    def _step_ghost(self) -> None:
        diff = self._ghost_target - self._ghost_current
        if abs(diff) < 0.006:
            self._ghost_current = self._ghost_target
            self._ghost_timer.stop()
        else:
            self._ghost_current += diff * 0.10
        self.setWindowOpacity(max(0.05, min(1.0, self._ghost_current)))

    # ── Boot animation ────────────────────────────────────────────────────────

    def _reveal_panels(self) -> None:
        """Stagger-fade each panel in after boot reactor completes."""
        panels  = [self._log, self._core, self._bio, self._graph]
        delays  = [0, 80, 160, 280]
        for panel, delay in zip(panels, delays):
            def _start(w=panel):
                anim = QPropertyAnimation(w, b"panel_opacity", self)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setDuration(420)
                anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
                anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
                self._panel_anims.append(anim)
            QTimer.singleShot(delay, _start)

    def _on_boot_done(self) -> None:
        log.info("[HUD] Boot sequence complete.")
        _log_bridge.line.emit("JARVIS v2.5  boot complete", True)

    # ── paintEvent: global dark veil + ripples ─────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Very subtle dark overlay over entire desktop
        p.fillRect(self.rect(), QColor(0, 0, 0, 45))

        # Full-screen alert ripples
        if not self._ripples:
            return
        now_ms = QDateTime.currentMSecsSinceEpoch()
        cx, cy = self.width() / 2.0, self.height() / 2.0
        max_r  = max(self.width(), self.height()) * 0.85
        alive: list[dict] = []
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rip in self._ripples:
            t = (now_ms - rip["start_ms"]) / 900.0
            if t >= 1.0:
                continue
            c = QColor(rip["color"])
            c.setAlpha(int(90 * (1.0 - t)))
            p.setPen(QPen(c, 2.0))
            p.drawEllipse(QPointF(cx, cy), t * max_r, t * max_r)
            alive.append(rip)
        self._ripples = alive
        if not self._ripples:
            self._ripple_timer.stop()

    # ── Start ─────────────────────────────────────────────────────────────────

    def awaken(self) -> None:
        """Show window and launch boot sequence."""
        self.showFullScreen()
        self._boot.setGeometry(self.rect())
        self._boot.start()


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
                _log_bridge.line.emit(f"WS connected  {ws_url}", True)
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
                        color = str(msg.get("hud_color",
                                            _STAGE_COLORS.get(stage, _DEFAULT_COLOR)))
                        _anger_bridge.updated.emit(gauge, stage, color)
                        focus_engine.record_gauge(gauge)
                        score = focus_engine.current_score
                        _sys_state["focus"] = score
                        _focus_bridge.scored.emit(score)

                    elif mtype == "study_update":
                        data    = msg.get("data", {})
                        subject = str(data.get("subject", ""))
                        pct     = float(data.get("progress", 0.0))
                        if subject:
                            _study_bridge.updated.emit(
                                subject, pct, str(data.get("dday", ""))
                            )
                            _log_bridge.line.emit(
                                f"STUDY  {subject}  {pct:.0f}%", False
                            )

                    elif mtype == "status":
                        cpu  = float(msg.get("cpu_percent",  0.0))
                        mem  = float(msg.get("mem_percent",  0.0))
                        disk = float(msg.get("disk_percent", 0.0))
                        _sys_state.update(cpu=cpu, mem=mem, disk=disk)
                        _sys_bridge.updated.emit(cpu, mem, disk)

                    elif mtype == "calendar_data":
                        predictor.update_events(msg.get("events", []))

                    elif mtype == "location_update":
                        lat = float(msg.get("lat", 0.0))
                        lon = float(msg.get("lon", 0.0))
                        if lat or lon:
                            predictor.update_location(lat, lon)

                    elif mtype == "stealth_update":
                        _mute_bridge.changed.emit(
                            bool(msg.get("muted", False)),
                            str(msg.get("icon", "")),
                            str(msg.get("reason", "")),
                        )

                    elif mtype == "proactive_alert":
                        text = str(msg.get("message", ""))
                        _alert_bridge.flashed.emit(text)
                        _log_bridge.line.emit(f"ALERT  {text[:60]}", True)

                    elif mtype == "deploy_failed":
                        _log_bridge.line.emit("DEPLOY FAILED ⚠", True)

                    elif mtype == "system_update":
                        _log_bridge.line.emit("DEPLOY  push complete ✓", True)

        except Exception as exc:
            log.warning("[Overlay] WS error: %s — retry in %.0fs", exc, backoff)
            _log_bridge.line.emit(f"WS reconnect in {backoff:.0f}s", False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _run_ws_thread(ws_url: str, http_base: str) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_ws_receive_loop(ws_url, http_base))


# ── Entry point ───────────────────────────────────────────────────────────────

class JarvisOverlay:
    """Creates QApplication, shows JarvisFullHUD, starts WS thread. Blocks."""

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

        hud = JarvisFullHUD()

        ws_thread = threading.Thread(
            target=_run_ws_thread,
            args=(self._ws_url, self._http_base),
            daemon=True,
            name="jarvis-ws-overlay",
        )
        ws_thread.start()

        log.info("[Overlay] Awakening sequence start.")
        hud.awaken()
        sys.exit(app.exec())
