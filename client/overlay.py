"""
client/overlay.py — Transparent, click-through fullscreen overlay (PyQt6).

Visual: a single Fury Gauge chip fixed in the top-right corner of the
primary screen.  All other screen area is fully transparent and lets
mouse events pass through to whatever window is below.

Thread model:
  Main thread  → Qt event loop (QApplication.exec)
  WS thread    → asyncio loop receiving anger_update events from the backend
  Bridge       → _AngerBridge (QObject with pyqtSignal) safely crosses the
                 thread boundary: emit() from WS thread; slot runs in main thread.

Click-through mechanism:
  Qt.WindowType.WindowTransparentForInput makes the OS treat the window as if
  it doesn't exist for mouse/touch/pen input.  Works on X11, Wayland (partial),
  Win32, and macOS without platform-specific code.

RAM footprint:
  PyQt6 base ≈ 90 MB; Python + websockets ≈ 25 MB → ~115 MB total.
  Stays well under the 200 MB budget.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

log = logging.getLogger("jarvis.overlay")

# ── Stage colours — mirrors anger_engine.py _PROFILES ────────────────────────
_STAGE_COLORS: dict[str, str] = {
    "GENTLE":    "#22c55e",
    "SARCASTIC": "#90ee90",
    "STERN":     "#fbbf24",
    "FURIOUS":   "#ff6b35",
    "LOCKDOWN":  "#ef4444",
}
_DEFAULT_COLOR = "#22c55e"

# ── Shared inter-thread queue (WS thread → Qt main thread) ────────────────────
_anger_q: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()


class _AngerBridge(QObject):
    """
    QObject living in the main thread.
    Emitting its signal from the WS thread is safe — Qt queues the delivery.
    """
    updated = pyqtSignal(float, str, str)   # gauge, stage, hud_color


# Module-level singleton — connected to FuryGauge in main thread
_bridge = _AngerBridge()


# ── Fury Gauge widget ─────────────────────────────────────────────────────────

class FuryGauge(QWidget):
    """
    Translucent pill widget — top-right corner.

    Paints itself on a fully transparent backing window so only the
    pill rectangle is visible; the rest of the screen sees through.
    """

    _PILL_W = 230
    _PILL_H = 62

    def __init__(self) -> None:
        super().__init__(None)

        # ── Window flags: frameless, always-on-top, tool (no taskbar), click-through
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self._PILL_W, self._PILL_H)

        # ── Interior layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(5)

        self._label = QLabel("GENTLE  0%")
        self._label.setStyleSheet(self._label_style(_DEFAULT_COLOR))
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(7)
        self._bar.setStyleSheet(self._bar_style(_DEFAULT_COLOR))
        layout.addWidget(self._bar)

        # ── Current state
        self._current_color = _DEFAULT_COLOR
        self._lockdown = False

        # ── Connect signal from WS thread
        _bridge.updated.connect(self._on_anger_updated)

    # ── Signal slot (runs in main thread) ─────────────────────────────────────

    def _on_anger_updated(self, gauge: float, stage: str, color: str) -> None:
        self._bar.setValue(int(gauge))
        self._bar.setStyleSheet(self._bar_style(color))
        self._label.setStyleSheet(self._label_style(color))
        self._label.setText(f"{stage}  {gauge:.0f}%")
        self._current_color = color
        self._lockdown = (stage == "LOCKDOWN")
        self.update()  # trigger repaint

    # ── Custom painting (pill background) ────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg_alpha = 210 if self._lockdown else 165
        bg_color = QColor(60, 0, 0, bg_alpha) if self._lockdown else QColor(0, 0, 0, bg_alpha)

        painter.setBrush(bg_color)
        border_color = QColor(self._current_color)
        border_color.setAlpha(180)
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

    # ── Position ──────────────────────────────────────────────────────────────

    def place_top_right(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        margin = 18
        self.move(screen.right() - self.width() - margin, screen.top() + margin)

    # ── Style helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _bar_style(color: str) -> str:
        return (
            "QProgressBar { background: rgba(255,255,255,25); border-radius: 3px; border: none; }"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )

    @staticmethod
    def _label_style(color: str) -> str:
        return (
            f"color: {color}; "
            "font-family: 'Orbitron', 'Share Tech Mono', monospace; "
            "font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )


# ── WebSocket receiver (runs in a background asyncio thread) ──────────────────

async def _ws_receive_loop(ws_url: str) -> None:
    """
    Connects to the JARVIS backend WS, registers as 'overlay' device,
    and relays anger_update events to the Qt main thread via _bridge.
    Reconnects with exponential backoff on any error.
    """
    import websockets  # already in root requirements — not an extra dep

    backoff = 2.0
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                log.info("[Overlay] WS connected → %s", ws_url)
                await ws.send(json.dumps({"type": "register", "device": "overlay"}))
                backoff = 2.0  # reset on successful connect

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") != "anger_update":
                        continue

                    gauge = float(msg.get("gauge", 0.0))
                    stage = str(msg.get("stage", "GENTLE"))
                    color = str(msg.get("hud_color", _STAGE_COLORS.get(stage, _DEFAULT_COLOR)))

                    # Cross-thread signal emit — Qt queues delivery to main thread
                    _bridge.updated.emit(gauge, stage, color)

        except Exception as exc:
            log.warning("[Overlay] WS error: %s — reconnect in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _run_ws_thread(ws_url: str) -> None:
    """Thread target: owns its own asyncio event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_ws_receive_loop(ws_url))


# ── Top-level orchestrator ────────────────────────────────────────────────────

class JarvisOverlay:
    """
    Creates QApplication, shows the FuryGauge chip, starts the WS receiver
    thread, and enters the Qt event loop.

    Call run() from your main entry point — it blocks until the app exits.
    """

    def __init__(self, ws_url: str = "ws://158.180.78.104:8000/ws") -> None:
        self._ws_url = ws_url

    def run(self) -> None:
        import sys
        app = QApplication.instance() or QApplication(sys.argv)

        gauge = FuryGauge()
        gauge.place_top_right()
        gauge.show()

        # Daemon thread — killed automatically when main thread exits
        ws_thread = threading.Thread(
            target=_run_ws_thread,
            args=(self._ws_url,),
            daemon=True,
            name="jarvis-ws-overlay",
        )
        ws_thread.start()

        log.info("[Overlay] Qt loop starting. WS thread launched.")
        sys.exit(app.exec())
