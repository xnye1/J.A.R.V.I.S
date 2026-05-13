"""
client/overlay.py — J.A.R.V.I.S. v2.5  High-Fidelity Motion HUD

Full-screen transparent overlay with Bento-Box grid + cinematic animations:

  ┌─────────────────┬──────────────────────────────┬──────────────────┐
  │                 │                              │                  │
  │  LogStream      │       AwakeningCore           │  BioRhythm       │
  │  (liquid scroll)│   (breathing arc reactor)    │ (rolling gauges) │
  │                 │                              │                  │
  │                 ├──────────────────────────────┴──────────────────┤
  │                 │          FocusHistoryGraph                      │
  └─────────────────┴─────────────────────────────────────────────────┘

Cinematic Boot (≈ 3.2 s):
  0–650 ms   : Arc reactor OutBack bounce-expansion
  680 ms     : Typewriter scan lines
  1 150 ms   : 4 panels stagger-reveal (opacity + Y-slide, 80 ms apart)
  1 600 ms   : Welcome message fade-in
  2 800 ms   : BootScreen InQuad fade-out
  3 200 ms   : Boot done + 220 ms glitch flash

Motion inventory:
  AnimatedValue      — 500 ms OutExpo rolling-number interpolation
  ArcReactorCore     — QPropertyAnimation rotation_angle (speed ∝ FocusScore)
                       QSequentialAnimationGroup 2 s InOutSine breathing glow
  LogStreamWidget    — 16 ms exponential-decay pixel scroll on new line
  GlassPanel         — panel_opacity + slide_offset (both pyqtProperty)
  JarvisFullHUD      — hud_opacity QPropertyAnimation 800 ms ghost fade
                       QGraphicsDropShadowEffect on every panel

Thread model: Qt main + asyncio WS thread, bridged via pyqtSignal (QueuedConnection).
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
    QSequentialAnimationGroup,
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
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QWidget,
)

log = logging.getLogger("jarvis.overlay")

# ── Palette ───────────────────────────────────────────────────────────────────

_C_BG     = QColor(4,   8,  22, 188)
_C_BORDER = QColor(0,  175, 255,  85)
_C_CYAN   = "#00b4ff"
_C_GREEN  = "#22c55e"
_C_LIME   = "#4ade80"
_C_YELLOW = "#fbbf24"
_C_ORANGE = "#ff6b35"
_C_RED    = "#ef4444"
_C_DIM    = QColor(140, 160, 200,  90)

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
    updated = pyqtSignal(float, str, str)

class _StudyBridge(QObject):
    updated = pyqtSignal(str, float, str)

class _GhostBridge(QObject):
    toggled = pyqtSignal(bool)

class _AlertBridge(QObject):
    flashed = pyqtSignal(str)

class _MuteBridge(QObject):
    changed = pyqtSignal(bool, str, str)

class _FocusBridge(QObject):
    scored = pyqtSignal(float)

class _SysBridge(QObject):
    updated = pyqtSignal(float, float, float)

class _LogBridge(QObject):
    line = pyqtSignal(str, bool)

class _SpeakBridge(QObject):
    # text, importance ("normal"/"warning"/"critical"), estimated_ms
    started  = pyqtSignal(str, str, int)
    finished = pyqtSignal()

class _FlickerBridge(QObject):
    # True = start power-surge flicker, False = stop
    flickering = pyqtSignal(bool)


_anger_bridge   = _AngerBridge()
_study_bridge = _StudyBridge()
_ghost_bridge = _GhostBridge()
_alert_bridge = _AlertBridge()
_mute_bridge  = _MuteBridge()
_focus_bridge = _FocusBridge()
_sys_bridge     = _SysBridge()
_log_bridge     = _LogBridge()
_speak_bridge   = _SpeakBridge()
_flicker_bridge = _FlickerBridge()

_sys_state: dict = {"cpu": 0.0, "mem": 0.0, "disk": 0.0, "focus": 50.0}

# ── AnimatedValue — rolling-number interpolator ───────────────────────────────

class AnimatedValue(QObject):
    """
    Float value that smoothly rolls to a new target over `duration_ms`.
    value_changed is emitted on every animation tick so callers can call update().
    """
    value_changed = pyqtSignal()

    def __init__(
        self,
        initial:     float = 0.0,
        duration_ms: int   = 500,
        parent:      QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._val  = float(initial)
        self._anim = QPropertyAnimation(self, b"current", self)
        self._anim.setDuration(duration_ms)
        self._anim.setEasingCurve(QEasingCurve.Type.OutExpo)

    @pyqtProperty(float)
    def current(self) -> float:
        return self._val

    @current.setter  # type: ignore[no-redef]
    def current(self, v: float) -> None:
        self._val = v
        self.value_changed.emit()

    @property
    def display(self) -> float:
        return self._val

    def set_target(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._val)
        self._anim.setEndValue(float(target))
        self._anim.start()

# ── Neon glow helpers ─────────────────────────────────────────────────────────

_NEON_ARC  = [(12, 14), (7, 38), (4, 95), (2, 220)]
_NEON_THIN = [(7,  14), (4, 40), (2, 90), (1, 200)]


def _neon_arc(
    p: QPainter,
    rect: QRectF,
    start_deg: float,
    span_deg: float,
    color: str | QColor,
    layers: list = _NEON_ARC,
) -> None:
    base = QColor(color)
    for width, alpha in layers:
        c = QColor(base); c.setAlpha(alpha)
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
    base = QColor(color)
    for width, alpha in layers:
        c = QColor(base); c.setAlpha(alpha)
        p.setPen(QPen(c, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, rx + width / 2, ry + width / 2)

# ── GlassPanel base class ─────────────────────────────────────────────────────

class GlassPanel(QWidget):
    """
    Base for all HUD panels.
    pyqtProperties:
      panel_opacity  (0→1)   — boot fade-in animation target
      slide_offset   (float) — Y-pixel slide; positive = shifted DOWN
    Call _begin_paint(p) at the top of every paintEvent.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._panel_opacity: float = 0.0
        self._slide_offset:  float = 0.0

        # Flicker state — power-surge border animation while JARVIS speaks
        self._flicker_mod: float = 0.0
        self._flicker_timer = QTimer(self)
        self._flicker_timer.setInterval(50)   # 20 fps
        self._flicker_timer.timeout.connect(self._tick_flicker)
        _flicker_bridge.flickering.connect(self._on_flicker)

    # ── Qt properties ─────────────────────────────────────────────────────────

    @pyqtProperty(float)
    def panel_opacity(self) -> float:
        return self._panel_opacity

    @panel_opacity.setter  # type: ignore[no-redef]
    def panel_opacity(self, v: float) -> None:
        self._panel_opacity = max(0.0, min(1.0, v))
        self.update()

    @pyqtProperty(float)
    def slide_offset(self) -> float:
        return self._slide_offset

    @slide_offset.setter  # type: ignore[no-redef]
    def slide_offset(self, v: float) -> None:
        self._slide_offset = v
        self.update()

    # ── Paint helpers ─────────────────────────────────────────────────────────

    def _on_flicker(self, active: bool) -> None:
        if active:
            self._flicker_timer.start()
        else:
            self._flicker_timer.stop()
            self._flicker_mod = 0.0
            self.update()

    def _tick_flicker(self) -> None:
        """Power-surge: random spikes with exponential decay, ~20 fps."""
        if random.random() > 0.72:
            self._flicker_mod = random.uniform(-0.55, 1.4)
        else:
            self._flicker_mod *= 0.50
        self.update()

    def _begin_paint(self, p: QPainter) -> None:
        """Apply opacity + slide translation. Call once at paintEvent start."""
        p.setOpacity(self._panel_opacity)
        if self._slide_offset:
            p.translate(0.0, self._slide_offset)

    def _paint_bg(self, p: QPainter) -> None:
        """Frosted glass card: dark fill + top sheen + flicker-reactive border."""
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        p.fillRect(self.rect(), _C_BG)
        p.fillRect(0, 0, self.width(), 1, QColor(255, 255, 255, 28))
        p.setClipping(False)
        # Border alpha surges with flicker_mod (85 ± up to 120)
        border_c = QColor(_C_BORDER)
        border_c.setAlpha(min(255, max(15, int(85 + self._flicker_mod * 120))))
        p.setPen(QPen(border_c, 1.0 + max(0.0, self._flicker_mod * 0.8)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(clip)


# ── ArcReactorCore ────────────────────────────────────────────────────────────

class ArcReactorCore(GlassPanel):
    """
    Center-top panel: breathing arc reactor + identity.

    Animations:
      rotation_angle  : QPropertyAnimation, infinite loop, speed ∝ FocusScore
      breath_val      : QSequentialAnimationGroup 0.3↔1.0 InOutSine, 4 s cycle
      _av_fury/_av_focus: AnimatedValue for rolling-number display
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._angle:      float = 0.0
        self._color:      str   = _DEFAULT_COLOR
        self._stage:      str   = "GENTLE"
        self._breath_val: float = 0.5

        # Voice reactivity state
        self._is_speaking: bool  = False
        self._voice_raw:   float = 0.0   # smoothed oscillator
        self._voice_amp:   float = 0.0   # current amplitude (0-1)
        self._importance:  str   = "normal"

        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(30)   # ~33 fps waveform update
        self._voice_timer.timeout.connect(self._tick_voice)

        # Rolling display values
        self._av_fury  = AnimatedValue(0.0,  500, self)
        self._av_focus = AnimatedValue(50.0, 500, self)
        self._av_fury.value_changed.connect(self.update)
        self._av_focus.value_changed.connect(self.update)

        # Rotation
        self._spin_anim = QPropertyAnimation(self, b"rotation_angle", self)
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(360.0)
        self._spin_anim.setDuration(1800)
        self._spin_anim.setLoopCount(-1)
        self._spin_anim.start()

        # Breathing glow: forward 0.3→1.0 + backward 1.0→0.3, infinite loop
        fwd = QPropertyAnimation(self, b"breath_val", self)
        fwd.setStartValue(0.3); fwd.setEndValue(1.0)
        fwd.setDuration(2000)
        fwd.setEasingCurve(QEasingCurve.Type.InOutSine)

        bwd = QPropertyAnimation(self, b"breath_val", self)
        bwd.setStartValue(1.0); bwd.setEndValue(0.3)
        bwd.setDuration(2000)
        bwd.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._breath_group = QSequentialAnimationGroup(self)
        self._breath_group.addAnimation(fwd)
        self._breath_group.addAnimation(bwd)
        self._breath_group.setLoopCount(-1)
        self._breath_group.start()

    # ── Qt properties ─────────────────────────────────────────────────────────

    @pyqtProperty(float)
    def rotation_angle(self) -> float:
        return self._angle

    @rotation_angle.setter  # type: ignore[no-redef]
    def rotation_angle(self, v: float) -> None:
        self._angle = v % 360.0
        self.update()

    @pyqtProperty(float)
    def breath_val(self) -> float:
        return self._breath_val

    @breath_val.setter  # type: ignore[no-redef]
    def breath_val(self, v: float) -> None:
        self._breath_val = v
        self.update()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_focus(self, score: float) -> None:
        self._av_focus.set_target(score)
        target_ms = max(350, min(3000, int(3000 - score * 26)))
        if abs(self._spin_anim.duration() - target_ms) > 60:
            pct = self._spin_anim.currentTime() / max(self._spin_anim.duration(), 1)
            self._spin_anim.pause()
            self._spin_anim.setDuration(target_ms)
            self._spin_anim.setCurrentTime(int(pct * target_ms))
            self._spin_anim.resume()

    def set_anger(self, gauge: float, stage: str, color: str) -> None:
        self._av_fury.set_target(gauge)
        self._stage = stage
        self._color = color
        self.update()

    # Importance → peak voice amplitude multiplier
    _IMP_AMP: dict[str, float] = {
        "normal":   0.15,
        "warning":  0.28,
        "critical": 0.52,
    }

    def start_speaking(self, importance: str = "normal") -> None:
        self._is_speaking = True
        self._importance  = importance
        if not self._voice_timer.isActive():
            self._voice_timer.start()

    def stop_speaking(self) -> None:
        self._is_speaking = False   # timer keeps running until decay → 0

    def _tick_voice(self) -> None:
        """
        Simulate voice waveform: sine oscillation at speech-rate freq + noise.
        Smooth with α=0.45 low-pass filter.  Decays on stop_speaking().
        """
        if self._is_speaking:
            freq   = 7.5 + random.uniform(-2.0, 2.5)   # 5.5–10 Hz speech range
            target = (
                0.50
                + 0.40 * math.sin(time.time() * freq * math.pi)
                + random.uniform(-0.08, 0.08)
            )
            target = max(0.0, min(1.0, target))
            self._voice_raw = self._voice_raw * 0.55 + target * 0.45
        else:
            self._voice_raw *= 0.86   # exponential decay after speaking ends

        self._voice_amp = self._voice_raw
        if self._voice_amp < 0.004 and not self._is_speaking:
            self._voice_timer.stop()
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._begin_paint(p)
        self._paint_bg(p)

        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0 - 18
        r_outer = min(w, h) * 0.23
        self._draw_reactor(p, cx, cy, r_outer)

        # Identity text
        p.setOpacity(self._panel_opacity * 0.9)
        p.setFont(QFont("Orbitron, Share Tech Mono, monospace", 13, QFont.Weight.Bold))
        p.setPen(QColor(_C_CYAN))
        p.drawText(
            QRectF(0, cy + r_outer + 18, w, 28),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "J.A.R.V.I.S.",
        )
        p.setFont(QFont("Share Tech Mono, monospace", 9))
        p.setPen(_C_DIM)
        p.drawText(
            QRectF(0, cy + r_outer + 46, w, 20),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            f"{self._stage}  ·  focus {self._av_focus.display:.0f}%",
        )

    def _draw_reactor(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        color = self._color

        # Outer orbit ring
        _neon_ellipse(p, QPointF(cx, cy), r, r, color, _NEON_THIN)

        # Three neon arcs
        arc_r    = r - 4
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        for i in range(3):
            _neon_arc(p, arc_rect, self._angle + i * 120.0, 95.0, color)

        # Mid ring
        _neon_ellipse(p, QPointF(cx, cy), r * 0.52, r * 0.52, color, _NEON_THIN)

        # Core glow — breath + voice scale pulse
        imp_fac   = self._IMP_AMP.get(self._importance, 0.15)
        voice_add = self._voice_amp * imp_fac
        # Radius expands with voice amplitude (more important = bigger pulse)
        core_r    = r * 0.26 * (1.0 + voice_add * 0.85)
        core_alpha = min(255, int(160 + 90 * self._breath_val + 65 * voice_add))
        core_c    = QColor(color); core_c.setAlpha(core_alpha)
        p.setBrush(core_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # Extra soft halo that blooms during speech
        if self._voice_amp > 0.06:
            halo_c = QColor(color)
            halo_c.setAlpha(int(45 * self._voice_amp * imp_fac / 0.15))
            p.setBrush(halo_c)
            p.drawEllipse(QPointF(cx, cy), core_r * 1.65, core_r * 1.65)

        # Bright centre dot (breathes + voice)
        dot_alpha = min(255, int(200 + 55 * self._breath_val + 45 * voice_add))
        p.setBrush(QColor(255, 255, 255, dot_alpha))
        p.drawEllipse(QPointF(cx, cy), core_r * 0.35, core_r * 0.35)

        # Fury outer arc (track + value)
        fury_r    = r + 14
        fury_rect = QRectF(cx - fury_r, cy - fury_r, fury_r * 2, fury_r * 2)
        p.setPen(QPen(QColor(255, 255, 255, 18), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(fury_rect, int(225 * 16), int(-270 * 16))
        fury_pct = self._av_fury.display
        if fury_pct > 0:
            _neon_arc(p, fury_rect, 225.0, -fury_pct / 100.0 * 270, color,
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
    """
    Left panel: scrolling log stream.
    New lines push existing content up with 16 ms exponential-decay pixel scroll.
    """

    _CAPACITY = 32
    _LINE_H   = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: deque[tuple[int, str, bool]] = deque(maxlen=self._CAPACITY)
        self._scroll_offset: float = 0.0

        # Smooth scroll timer (fires at 60 fps, stops when settled)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._tick_scroll)

        # Atmospheric log line generator
        self._atmos_timer = QTimer(self)
        self._atmos_timer.setInterval(2200)
        self._atmos_timer.timeout.connect(self._gen_atmospheric)
        self._atmos_timer.start()

        # Cursor blink (independent of scroll)
        self._cursor_on = True
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(500)
        self._cursor_timer.timeout.connect(self._blink)
        self._cursor_timer.start()

        _log_bridge.line.connect(self._append)

    def _blink(self) -> None:
        self._cursor_on = not self._cursor_on
        self.update()

    def _append(self, text: str, important: bool) -> None:
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._lines.append((QDateTime.currentMSecsSinceEpoch(),
                             f"[{ts}] {text}", important))
        # Start pixel-scroll: content will slide up by one line height
        self._scroll_offset = float(self._LINE_H)
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()
        self.update()

    def _tick_scroll(self) -> None:
        if self._scroll_offset > 0.3:
            self._scroll_offset *= 0.72   # exponential ease-out
            self.update()
        else:
            self._scroll_offset = 0.0
            self._scroll_timer.stop()
            self.update()

    def _gen_atmospheric(self) -> None:
        self._append(random.choice(_LOG_ATMOSPHERICS), False)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._begin_paint(p)
        self._paint_bg(p)

        if not self._lines:
            return

        pad    = 12
        now_ms = QDateTime.currentMSecsSinceEpoch()
        avail  = self.height() - 36 - pad
        max_vis = max(1, avail // self._LINE_H)
        visible = list(self._lines)[-max_vis:]

        p.setFont(QFont("Orbitron, monospace", 8, QFont.Weight.Bold))
        p.setPen(QColor(_C_CYAN))
        p.drawText(pad, 20, "SYS LOG")

        p.setFont(QFont("Share Tech Mono, Consolas, monospace", 8))

        for i, (ts_ms, text, important) in enumerate(visible):
            age_alpha = max(55, int(220 - (now_ms - ts_ms) / 1000.0 * 4))
            if important:
                c = QColor(_C_YELLOW); c.setAlpha(min(220, age_alpha + 40))
            elif i == len(visible) - 1:
                c = QColor(_C_GREEN);  c.setAlpha(age_alpha)
            else:
                c = QColor(_C_CYAN);   c.setAlpha(age_alpha)
            p.setPen(c)

            # Apply smooth scroll offset: lines slide UP as new line arrives
            y = 36 + i * self._LINE_H - self._scroll_offset
            p.drawText(
                QRectF(pad, y, self.width() - pad * 2, self._LINE_H),
                Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        # Blinking cursor on last line
        if self._cursor_on and visible:
            cursor_y = 36 + len(visible) * self._LINE_H - self._scroll_offset
            p.setPen(QColor(_C_GREEN))
            p.drawText(pad, cursor_y + self._LINE_H - 3, "▌")


# ── BioRhythmCard ─────────────────────────────────────────────────────────────

class BioRhythmCard(GlassPanel):
    """Right-top panel: four neon gauges with rolling-number animation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors = {
            "FOCUS": _C_CYAN,
            "FURY":  _C_ORANGE,
            "CPU":   _C_GREEN,
            "MEM":   _C_YELLOW,
        }
        self._avals: dict[str, AnimatedValue] = {
            k: AnimatedValue(50.0 if k == "FOCUS" else 0.0, 500, self)
            for k in self._colors
        }
        for av in self._avals.values():
            av.value_changed.connect(self.update)

    def set_focus(self, v: float) -> None:
        self._avals["FOCUS"].set_target(v)

    def set_anger(self, v: float) -> None:
        self._avals["FURY"].set_target(v)

    def set_sys(self, cpu: float, mem: float, _disk: float) -> None:
        self._avals["CPU"].set_target(cpu)
        self._avals["MEM"].set_target(mem)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._begin_paint(p)
        self._paint_bg(p)

        p.setFont(QFont("Orbitron, monospace", 8, QFont.Weight.Bold))
        p.setPen(QColor(_C_CYAN))
        p.drawText(14, 20, "BIO RHYTHM")

        w, h  = self.width(), self.height()
        pad   = 14
        th    = 30
        gap   = 10
        aw    = w - pad * 2 - gap
        ah    = h - th  - pad - gap
        cw    = aw // 2
        ch    = ah // 2
        g_r   = min(cw, ch) // 2 - 10

        for idx, key in enumerate(self._colors):
            col = idx % 2
            row = idx // 2
            cx  = pad + col * (cw + gap) + cw // 2
            cy  = th  + row * (ch + gap) + ch // 2
            self._draw_gauge(p, cx, cy, g_r, key,
                             self._avals[key].display, self._colors[key])

    def _draw_gauge(
        self, p: QPainter, cx: int, cy: int, r: int,
        label: str, value: float, color: str,
    ) -> None:
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        # Background track
        p.setPen(QPen(QColor(255, 255, 255, 22), 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(225 * 16), int(-270 * 16))
        # Value arc with neon glow
        if value > 0:
            _neon_arc(p, rect, 225.0, -value / 100.0 * 270, color)
        # Centre value (rolling number)
        p.setPen(QColor(color))
        p.setFont(QFont("Orbitron, monospace", max(9, int(r * 0.38)), QFont.Weight.Bold))
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
    """Bottom-right panel: scrolling EWMA focus line chart."""

    _CAPACITY = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: deque[float] = deque(maxlen=self._CAPACITY)
        for _ in range(20):
            self._history.append(50.0 + random.uniform(-3, 3))

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self.update)
        self._tick_timer.start()

    def push(self, score: float) -> None:
        self._history.append(score)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._begin_paint(p)
        self._paint_bg(p)

        w, h = self.width(), self.height()
        pl, pr, pt, pb = 48, 18, 30, 22

        p.setFont(QFont("Orbitron, monospace", 8, QFont.Weight.Bold))
        p.setPen(QColor(_C_CYAN))
        p.drawText(pl, 20, "FOCUS HISTORY  (EWMA)")

        gw = w - pl - pr
        gh = h - pt - pb

        def _to_xy(i: int, v: float, n: int):
            x = pl + gw * i / max(n - 1, 1)
            y = pt + gh - gh * max(0.0, min(100.0, v)) / 100.0
            return x, y

        # Grid
        p.setPen(QPen(QColor(0, 180, 255, 22), 1))
        for pct in (25, 50, 75, 100):
            y = pt + gh - gh * pct / 100
            p.drawLine(pl, int(y), pl + gw, int(y))

        # Thresholds
        for v, c, lbl in [(70.0, _C_CYAN, "GHOST"), (60.0, _C_YELLOW, "BREAK")]:
            y = int(pt + gh - gh * v / 100)
            p.setPen(QPen(QColor(c), 1, Qt.PenStyle.DashLine))
            p.drawLine(pl, y, pl + gw, y)
            p.setFont(QFont("Share Tech Mono, monospace", 7))
            p.setPen(QColor(c)); p.drawText(2, y + 4, lbl)

        # Y labels
        p.setFont(QFont("Share Tech Mono, monospace", 7))
        p.setPen(_C_DIM)
        for pct in (0, 25, 50, 75, 100):
            p.drawText(1, int(pt + gh - gh * pct / 100) + 4, str(pct))

        pts = list(self._history)
        n   = len(pts)
        if n < 2:
            return

        # Area fill
        fill = QPainterPath()
        x0, y0 = _to_xy(0, pts[0], n)
        fill.moveTo(x0, pt + gh)
        fill.lineTo(x0, y0)
        for i, v in enumerate(pts[1:], 1):
            fill.lineTo(*_to_xy(i, v, n))
        fill.lineTo(_to_xy(n - 1, pts[-1], n)[0], pt + gh)
        fill.closeSubpath()
        grad = QLinearGradient(0, pt, 0, pt + gh)
        grad.setColorAt(0.0, QColor(0, 200, 100, 55))
        grad.setColorAt(1.0, QColor(0, 200, 100,  0))
        p.fillPath(fill, grad)

        # Neon line (3-pass glow)
        line = QPainterPath()
        line.moveTo(*_to_xy(0, pts[0], n))
        for i, v in enumerate(pts[1:], 1):
            line.lineTo(*_to_xy(i, v, n))
        for width, alpha in [(5, 18), (3, 55), (1.5, 210)]:
            c = QColor(_C_GREEN); c.setAlpha(alpha)
            p.setPen(QPen(c, width)); p.drawPath(line)

        # Live dot
        lx, ly = _to_xy(n - 1, pts[-1], n)
        p.setBrush(QColor(_C_GREEN))
        p.setPen(QPen(QColor(255, 255, 255, 180), 1))
        p.drawEllipse(QPointF(lx, ly), 4.0, 4.0)
        p.setFont(QFont("Orbitron, monospace", 9, QFont.Weight.Bold))
        p.setPen(QColor(_C_GREEN))
        p.drawText(int(lx) + 8, int(ly) + 4, f"{pts[-1]:.0f}")


# ── MessageBubble ─────────────────────────────────────────────────────────────

_IMP_COLORS = {
    "normal":   _C_CYAN,
    "warning":  _C_YELLOW,
    "critical": _C_RED,
}

class MessageBubble(QWidget):
    """
    Floating center-screen text card that appears when JARVIS speaks.

    On show_message():
      1. Three ripple circles expand outward from the card center (150 ms apart)
      2. Card + text fade in (300 ms InOutQuad)
      3. Auto-fades out after `duration_ms`

    Importance colours:
      normal   → cyan
      warning  → yellow
      critical → red
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._text:       str   = ""
        self._importance: str   = "normal"
        self._opacity:    float = 0.0
        self._ripples:    list[dict] = []

        # Ripple repaint timer (33 ms ≈ 30 fps, stops when empty)
        self._rip_timer = QTimer(self)
        self._rip_timer.setInterval(33)
        self._rip_timer.timeout.connect(self.update)

        # Fade-in / fade-out animation
        self._fade_anim = QPropertyAnimation(self, b"bubble_opacity", self)
        self._fade_anim.setDuration(300)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.finished.connect(self._on_fade_done)

        # Auto-hide countdown
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fadeout)

        self.hide()

    # ── Qt property ───────────────────────────────────────────────────────────

    @pyqtProperty(float)
    def bubble_opacity(self) -> float:
        return self._opacity

    @bubble_opacity.setter  # type: ignore[no-redef]
    def bubble_opacity(self, v: float) -> None:
        self._opacity = v
        self.update()

    # ── Public API ────────────────────────────────────────────────────────────

    def show_message(self, text: str, importance: str, duration_ms: int) -> None:
        self._text       = text[:88] + ("…" if len(text) > 88 else "")
        self._importance = importance
        self._reposition()

        # Three staggered ripples
        for i in range(3):
            QTimer.singleShot(
                i * 160,
                lambda: self._ripples.append({
                    "start_ms": QDateTime.currentMSecsSinceEpoch(),
                    "color": _IMP_COLORS.get(self._importance, _C_CYAN),
                }),
            )
        if not self._rip_timer.isActive():
            self._rip_timer.start()

        # Fade in
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self.show()
        self.raise_()

        # Schedule fade-out
        self._hide_timer.stop()
        self._hide_timer.start(duration_ms)

    def _reposition(self) -> None:
        if self.parent():
            pw = self.parent().width()
            ph = self.parent().height()
            self.setGeometry(pw // 2 - 380, int(ph * 0.40), 760, 78)

    def _start_fadeout(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def _on_fade_done(self) -> None:
        if self._opacity <= 0.01:
            self._rip_timer.stop()
            self.hide()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._opacity)

        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        color  = _IMP_COLORS.get(self._importance, _C_CYAN)

        # ── Ripples (behind card) ──────────────────────────────────────────────
        now_ms = QDateTime.currentMSecsSinceEpoch()
        alive: list[dict] = []
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rip in self._ripples:
            t = (now_ms - rip["start_ms"]) / 820.0
            if t >= 1.0:
                continue
            max_r = max(w, h) * 0.90
            rc    = QColor(rip["color"]); rc.setAlpha(int(115 * (1.0 - t)))
            p.setPen(QPen(rc, 1.8))
            p.drawEllipse(QPointF(cx, cy), t * max_r, t * max_r)
            alive.append(rip)
        self._ripples = alive
        if not self._ripples:
            self._rip_timer.stop()

        # ── Card ──────────────────────────────────────────────────────────────
        card = QPainterPath()
        card.addRoundedRect(QRectF(0, 0, w, h), 12, 12)
        p.setClipPath(card)
        p.fillRect(self.rect(), QColor(4, 8, 22, 205))
        p.fillRect(0, 0, w, 1, QColor(255, 255, 255, 30))
        p.setClipping(False)

        bc = QColor(color); bc.setAlpha(170)
        p.setPen(QPen(bc, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(card)

        # Left accent bar
        bar_c = QColor(color); bar_c.setAlpha(200)
        p.fillRect(0, 10, 3, h - 20, bar_c)

        # ── Text ──────────────────────────────────────────────────────────────
        p.setPen(QColor(color))
        p.setFont(QFont("Orbitron, Share Tech Mono, monospace", 11, QFont.Weight.Bold))
        p.drawText(
            QRectF(18, 0, w - 36, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )


# ── BootScreen ────────────────────────────────────────────────────────────────

class BootScreen(QWidget):
    """
    Full-size child overlay driving the Awakening boot sequence.

    Reactor expansion uses OutBack easing for a bouncy, energetic reveal.
    Emits panels_reveal at 1 150 ms and boot_done at ~3 200 ms.
    """

    panels_reveal = pyqtSignal()
    boot_done     = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(parent.rect())

        self._reactor_sc: float = 0.0
        self._fade_op:    float = 1.0
        self._scan_lines: list[str] = ["", ""]
        self._full_scan   = ["INITIATING NEURAL NETWORK...", "USER IDENTIFIED:  Sir"]
        self._scan_idx    = [0, 0]
        self._welcome_op: float = 0.0
        self._angle:      float = 0.0

        # Boot-reactor spin (independent of main reactor)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._tick_spin)

        # OutBack reactor expansion  ← key change: OutCubic → OutBack
        self._sc_anim = QPropertyAnimation(self, b"reactor_scale", self)
        self._sc_anim.setStartValue(0.0)
        self._sc_anim.setEndValue(1.0)
        self._sc_anim.setDuration(650)
        self._sc_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        # Fade-out
        self._fade_anim = QPropertyAnimation(self, b"fade_opacity", self)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setDuration(500)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self._fade_anim.finished.connect(self._on_fade_done)

        # Typewriter timers
        self._type_timers = [QTimer(self), QTimer(self)]
        for i, t in enumerate(self._type_timers):
            t.setInterval(55)
            t.timeout.connect(lambda _, li=i: self._type(li))

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
        self._reactor_sc = v; self.update()

    @pyqtProperty(float)
    def fade_opacity(self) -> float:
        return self._fade_op

    @fade_opacity.setter  # type: ignore[no-redef]
    def fade_opacity(self, v: float) -> None:
        self._fade_op = v; self.update()

    # ── Sequence control ──────────────────────────────────────────────────────

    def start(self) -> None:
        self.show(); self.raise_()
        self._spin_timer.start()
        self._sc_anim.start()
        QTimer.singleShot(680,  self._begin_scan_line0)
        QTimer.singleShot(1050, self._begin_scan_line1)
        QTimer.singleShot(1150, self.panels_reveal.emit)
        QTimer.singleShot(1600, self._begin_welcome)
        QTimer.singleShot(2800, self._begin_fadeout)

    def _begin_scan_line0(self) -> None: self._type_timers[0].start()
    def _begin_scan_line1(self) -> None: self._type_timers[1].start()
    def _begin_welcome(self)   -> None: self._welcome_timer.start()
    def _begin_fadeout(self)   -> None: self._fade_anim.start()

    def _on_fade_done(self) -> None:
        self._spin_timer.stop()
        self._welcome_timer.stop()
        self.hide()
        self.boot_done.emit()

    def _tick_spin(self) -> None:
        self._angle = (self._angle + 3.0) % 360.0
        self.update()

    def _type(self, li: int) -> None:
        si = self._scan_idx[li]
        if si < len(self._full_scan[li]):
            self._scan_lines[li] = self._full_scan[li][: si + 1]
            self._scan_idx[li] += 1
            self.update()
        else:
            self._type_timers[li].stop()

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
        p.fillRect(self.rect(), QColor(2, 5, 15, 235))

        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0 - 60

        r = min(w, h) * 0.18 * self._reactor_sc
        if r > 2:
            self._draw_boot_reactor(p, cx, cy, r)

        # Scan text
        if self._scan_lines[0] or self._scan_lines[1]:
            font = QFont("Share Tech Mono, Consolas, monospace", 13)
            p.setFont(font)
            for i, txt in enumerate(self._scan_lines):
                if not txt:
                    continue
                c = QColor(_C_CYAN if i == 0 else _C_GREEN)
                cursor = "▌" if (self._type_timers[i].isActive()
                                  and int(time.time() * 4) % 2 == 0) else ""
                p.setPen(c)
                p.drawText(
                    QRectF(0, cy + r + 30 + i * 30, w, 30),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                    txt + cursor,
                )

        # Welcome message
        if self._welcome_op > 0.01:
            p.setOpacity(self._fade_op * self._welcome_op)
            p.setFont(QFont("Orbitron, monospace", 18, QFont.Weight.Bold))
            p.setPen(QColor(_C_CYAN))
            p.drawText(
                QRectF(0, h / 2.0 + 80, w, 44),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                "Welcome home, Sir.",
            )
            p.setFont(QFont("Share Tech Mono, monospace", 11))
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
        core_c = QColor(_C_CYAN); core_c.setAlpha(200)
        p.setBrush(core_c); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(cx, cy), core_r * 0.35, core_r * 0.35)


# ── JarvisFullHUD ─────────────────────────────────────────────────────────────

class JarvisFullHUD(QWidget):
    """
    Full-screen transparent overlay. Bento grid + cinematic polish.

    Ghost mode: 800 ms QPropertyAnimation on hud_opacity.
    Panel reveal: simultaneous panel_opacity + slide_offset animations.
    Boot glitch: 220 ms horizontal noise flash on boot completion.
    Drop shadows: QGraphicsDropShadowEffect on every panel.
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

        grid.addWidget(self._log,   0, 0, 2, 1)
        grid.addWidget(self._core,  0, 1, 1, 1)
        grid.addWidget(self._bio,   0, 2, 1, 1)
        grid.addWidget(self._graph, 1, 1, 1, 2)

        # Drop shadows — cyan glow gives each panel depth/separation
        self._apply_shadows()

        # ── Ghost mode: 800 ms smooth opacity via pyqtProperty ────────────────
        self._ghost_opacity: float = 0.88
        self.setWindowOpacity(self._ghost_opacity)

        self._ghost_anim = QPropertyAnimation(self, b"hud_opacity", self)
        self._ghost_anim.setDuration(800)
        self._ghost_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # ── Alert ripples ─────────────────────────────────────────────────────
        self._ripples: list[dict] = []
        self._ripple_timer = QTimer(self)
        self._ripple_timer.setInterval(33)
        self._ripple_timer.timeout.connect(self.update)

        # ── Glitch effect (post-boot flash) ───────────────────────────────────
        self._glitch_active = False
        self._glitch_timer  = QTimer(self)
        self._glitch_timer.setInterval(33)
        self._glitch_timer.timeout.connect(self.update)

        # ── Message bubble (floating JARVIS speech card) ──────────────────────
        self._msg_bubble = MessageBubble(self)

        # ── Boot screen ───────────────────────────────────────────────────────
        self._boot = BootScreen(self)
        self._boot.panels_reveal.connect(self._reveal_panels)
        self._boot.boot_done.connect(self._on_boot_done)
        self._panel_anims: list[QPropertyAnimation] = []

        # ── Bridge connections ────────────────────────────────────────────────
        _anger_bridge.updated.connect(self._on_anger)
        _focus_bridge.scored.connect(self._on_focus)
        _sys_bridge.updated.connect(self._on_sys)
        _alert_bridge.flashed.connect(self._on_alert)
        _ghost_bridge.toggled.connect(self._set_ghost)
        _speak_bridge.started.connect(self._on_speaking_started)
        _speak_bridge.finished.connect(self._on_speaking_done)

    # ── hud_opacity Qt property ───────────────────────────────────────────────

    @pyqtProperty(float)
    def hud_opacity(self) -> float:
        return self._ghost_opacity

    @hud_opacity.setter  # type: ignore[no-redef]
    def hud_opacity(self, v: float) -> None:
        self._ghost_opacity = v
        self.setWindowOpacity(max(0.05, min(1.0, v)))

    # ── Drop shadow setup ─────────────────────────────────────────────────────

    def _apply_shadows(self) -> None:
        configs = [
            (self._log,   QColor(0, 180, 255,  85), 22),
            (self._core,  QColor(0, 180, 255, 105), 28),
            (self._bio,   QColor(0, 140, 255,  80), 22),
            (self._graph, QColor(0, 200, 100,  70), 22),
        ]
        for widget, color, radius in configs:
            eff = QGraphicsDropShadowEffect(widget)
            eff.setBlurRadius(radius)
            eff.setColor(color)
            eff.setOffset(0, 2)
            widget.setGraphicsEffect(eff)

    # ── Data slots ────────────────────────────────────────────────────────────

    def _on_anger(self, gauge: float, stage: str, color: str) -> None:
        self._core.set_anger(gauge, stage, color)
        self._bio.set_anger(gauge)
        # LOCKDOWN stage → critical speaking pulse
        if stage == "LOCKDOWN" and not self._core._is_speaking:
            self._core.start_speaking("critical")
            QTimer.singleShot(4000, self._core.stop_speaking)

    def _on_speaking_started(self, text: str, importance: str, duration: int) -> None:
        """
        Activate vocal reactive visuals:
          - ArcReactorCore: voice-pulse at given importance amplitude
          - MessageBubble: ripple + text card
          - All GlassPanels: power-surge border flicker
        Duration auto-stops everything.
        """
        self._core.start_speaking(importance)
        self._msg_bubble.show_message(text, importance, duration)
        _flicker_bridge.flickering.emit(True)
        _log_bridge.line.emit(f"JARVIS [{importance.upper()}]: {text[:50]}", True)
        QTimer.singleShot(duration, self._on_speaking_done)

    def _on_speaking_done(self) -> None:
        self._core.stop_speaking()
        _flicker_bridge.flickering.emit(False)

    def _on_focus(self, score: float) -> None:
        self._core.set_focus(score)
        self._bio.set_focus(score)
        self._graph.push(score)

    def _on_sys(self, cpu: float, mem: float, disk: float) -> None:
        self._bio.set_sys(cpu, mem, disk)
        _log_bridge.line.emit(f"CPU {cpu:.0f}%  MEM {mem:.0f}%  DISK {disk:.0f}%", False)

    def _on_alert(self, _msg: str) -> None:
        now = QDateTime.currentMSecsSinceEpoch()
        self._ripples.append({"start_ms": now, "color": _C_YELLOW})
        QTimer.singleShot(200, lambda: self._ripples.append({
            "start_ms": QDateTime.currentMSecsSinceEpoch(), "color": _C_YELLOW}))
        if not self._ripple_timer.isActive():
            self._ripple_timer.start()
        self.update()

    def _set_ghost(self, ghost: bool) -> None:
        target = 0.12 if ghost else 0.88
        self._ghost_anim.stop()
        self._ghost_anim.setStartValue(self._ghost_opacity)
        self._ghost_anim.setEndValue(target)
        self._ghost_anim.start()

    # ── Boot animation ────────────────────────────────────────────────────────

    def _reveal_panels(self) -> None:
        """Stagger-reveal: opacity 0→1 + Y slide 32→0, 80 ms apart."""
        panels = [self._log, self._core, self._bio, self._graph]
        delays = [0, 80, 160, 280]
        for panel, delay in zip(panels, delays):
            def _start(w=panel) -> None:
                # Opacity
                op = QPropertyAnimation(w, b"panel_opacity", self)
                op.setStartValue(0.0); op.setEndValue(1.0)
                op.setDuration(440)
                op.setEasingCurve(QEasingCurve.Type.InOutQuad)
                op.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
                # Y slide-up (start below, ease to normal position)
                sl = QPropertyAnimation(w, b"slide_offset", self)
                sl.setStartValue(34.0); sl.setEndValue(0.0)
                sl.setDuration(480)
                sl.setEasingCurve(QEasingCurve.Type.OutCubic)
                sl.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
                self._panel_anims.extend([op, sl])
            QTimer.singleShot(delay, _start)

    def _on_boot_done(self) -> None:
        log.info("[HUD] Boot complete.")
        _log_bridge.line.emit("JARVIS v2.5  boot complete", True)
        # 220 ms glitch flash
        self._glitch_active = True
        self._glitch_timer.start()
        QTimer.singleShot(220, self._end_glitch)

    def _end_glitch(self) -> None:
        self._glitch_active = False
        self._glitch_timer.stop()
        self.update()

    # ── paintEvent ───────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Subtle desktop darkening
        p.fillRect(self.rect(), QColor(0, 0, 0, 45))

        # Boot glitch effect
        if self._glitch_active:
            self._draw_glitch(p)

        # Full-screen alert ripples
        if self._ripples:
            self._draw_ripples(p)

    def _draw_glitch(self, p: QPainter) -> None:
        """220 ms horizontal noise bands + occasional vertical strip."""
        w, h = self.width(), self.height()
        rng  = random.Random(int(time.time() * 22))   # seed → ~22 fps flicker
        for _ in range(16):
            y   = rng.randint(0, h - 1)
            ht  = rng.randint(1, 3)
            alp = rng.randint(12, 52)
            c   = QColor(_C_CYAN if rng.random() > 0.35 else "#ffffff")
            c.setAlpha(alp)
            p.fillRect(0, y, w, ht, c)
        if rng.random() > 0.60:
            x  = rng.randint(0, max(1, w - 100))
            gw = rng.randint(2, 8)
            c  = QColor(_C_CYAN); c.setAlpha(rng.randint(12, 35))
            p.fillRect(x, 0, gw, h, c)

    def _draw_ripples(self, p: QPainter) -> None:
        now_ms = QDateTime.currentMSecsSinceEpoch()
        cx, cy = self.width() / 2.0, self.height() / 2.0
        max_r  = max(self.width(), self.height()) * 0.85
        alive: list[dict] = []
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rip in self._ripples:
            t = (now_ms - rip["start_ms"]) / 900.0
            if t >= 1.0:
                continue
            c = QColor(rip["color"]); c.setAlpha(int(90 * (1.0 - t)))
            p.setPen(QPen(c, 2.0))
            p.drawEllipse(QPointF(cx, cy), t * max_r, t * max_r)
            alive.append(rip)
        self._ripples = alive
        if not self._ripples:
            self._ripple_timer.stop()

    # ── Entry ─────────────────────────────────────────────────────────────────

    def awaken(self) -> None:
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
                                subject, pct, str(data.get("dday", "")))
                            _log_bridge.line.emit(
                                f"STUDY  {subject}  {pct:.0f}%", False)

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

                    elif mtype == "chat_response":
                        text = str(msg.get("message", msg.get("response", "")))
                        if text:
                            duration = max(2200, len(text) * 55)
                            _speak_bridge.started.emit(text, "normal", duration)

                    elif mtype == "proactive_alert":
                        text     = str(msg.get("message", ""))
                        severity = str(msg.get("severity", "NORMAL")).upper()
                        imp      = ("critical" if severity == "CRITICAL"
                                    else "warning" if severity in ("HIGH", "WARNING")
                                    else "normal")
                        duration = max(3000, len(text) * 60)
                        _speak_bridge.started.emit(text, imp, duration)
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
