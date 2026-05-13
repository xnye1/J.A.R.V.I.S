"""
client/hotword.py — Always-On Hotword & Clap Detection

HotwordEngine:
  - Runs as a daemon thread; never blocks the Qt main thread
  - Two wake triggers: "자비스" keyword (openWakeWord ONNX) + double-clap (RMS burst)
  - Power-save mode: 8 kHz / 40 ms frames; normal: 16 kHz / 30 ms frames
  - webrtcvad gates silent frames → CPU near-zero when quiet
  - Emits WakeEvent via on_wake callback (must be thread-safe — use pyqtSignal.emit)

os_wake_display():
  - Wakes X11 DPMS display from sleep via xset
  - Falls back silently on non-X11 / no-xset systems
"""

from __future__ import annotations

import enum
import logging
import math
import struct
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("jarvis.hotword")


class WakeEvent(enum.Enum):
    HOTWORD = "hotword"   # "자비스" / hey_jarvis detected
    CLAP    = "clap"      # double-clap detected


# ── Optional heavy deps — degrade gracefully ─────────────────────────────────

try:
    import pyaudio as _pa_mod
    _HAS_PYAUDIO = True
except ImportError:
    _pa_mod = None  # type: ignore[assignment]
    _HAS_PYAUDIO = False
    log.warning("[Hotword] pyaudio not installed — hotword detection disabled")

try:
    import webrtcvad as _vad_mod
    _HAS_VAD = True
except ImportError:
    _vad_mod = None  # type: ignore[assignment]
    _HAS_VAD = False
    log.warning("[Hotword] webrtcvad not installed — VAD disabled (higher CPU use)")

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:
    from openwakeword.model import Model as _OWW_Model
    _HAS_OWW = True
except ImportError:
    _OWW_Model = None  # type: ignore[assignment,misc]
    _HAS_OWW = False
    log.warning("[Hotword] openwakeword not installed — clap-only wake mode")

# ── Audio config ──────────────────────────────────────────────────────────────

_NORMAL_RATE     = 16_000   # Hz
_POWERSAVE_RATE  =  8_000   # Hz
_NORMAL_MS       = 30       # frame length ms
_POWERSAVE_MS    = 40       # frame length ms
_CHANNELS        = 1

# Clap detection
_CLAP_RMS_THRESH = 0.22     # RMS > this → clap burst
_CLAP_WINDOW_S   = 0.80     # double-clap must fit within this window

# openWakeWord
_OWW_SCORE_THRESH = 0.62    # confidence threshold


# ── Display wake ──────────────────────────────────────────────────────────────

def os_wake_display() -> None:
    """Wake X11 display from DPMS sleep. No-op on non-X11 systems."""
    try:
        subprocess.run(
            ["xset", "dpms", "force", "on"],
            timeout=2,
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        pass


# ── Clap detector ─────────────────────────────────────────────────────────────

class ClapDetector:
    """RMS energy burst detector — two bursts within _CLAP_WINDOW_S = double-clap."""

    def __init__(self) -> None:
        self._last_clap_t:    float = 0.0
        self._prev_was_loud:  bool  = False

    def feed(self, pcm_s16: bytes) -> bool:
        """Return True when a double-clap is confirmed."""
        if not _HAS_NUMPY:
            return False
        samples = np.frombuffer(pcm_s16, dtype=np.int16).astype(np.float32) / 32768.0
        rms     = float(math.sqrt(max(0.0, float(np.mean(samples ** 2)))))

        is_loud = rms > _CLAP_RMS_THRESH
        triggered = False
        if is_loud and not self._prev_was_loud:
            now = time.monotonic()
            gap = now - self._last_clap_t
            if 0.05 < gap < _CLAP_WINDOW_S:
                # Second burst within window — confirmed double-clap
                self._last_clap_t = 0.0
                triggered = True
            else:
                self._last_clap_t = now
        self._prev_was_loud = is_loud
        return triggered


# ── Hotword engine ────────────────────────────────────────────────────────────

class HotwordEngine:
    """
    Daemon thread that listens on the microphone for:
      - "자비스" / hey_jarvis (openWakeWord ONNX model)
      - Double-clap (RMS burst detector)

    on_wake(WakeEvent) is called from the listening thread.
    Use a thread-safe bridge to reach the Qt main thread, e.g.:
        engine = HotwordEngine(on_wake=_wake_bridge.triggered.emit)
    """

    def __init__(
        self,
        on_wake:    Callable[[WakeEvent], None],
        power_save: bool = False,
    ) -> None:
        self._on_wake    = on_wake
        self._power_save = power_save
        self._running    = False
        self._thread:    threading.Thread | None = None
        self._oww_model: object | None = None

        if _HAS_OWW and _OWW_Model is not None:
            try:
                self._oww_model = _OWW_Model(
                    wakeword_models=["hey_jarvis"],
                    inference_framework="onnx",
                )
                log.info("[Hotword] openWakeWord loaded (hey_jarvis ONNX)")
            except Exception as exc:
                log.warning("[Hotword] OWW load failed: %s — clap-only mode", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def rate(self) -> int:
        return _POWERSAVE_RATE if self._power_save else _NORMAL_RATE

    @property
    def frame_ms(self) -> int:
        return _POWERSAVE_MS if self._power_save else _NORMAL_MS

    def set_power_save(self, enabled: bool) -> None:
        if self._power_save == enabled:
            return
        was_running = self._running
        if was_running:
            self.stop()
        self._power_save = enabled
        if was_running:
            self.start()

    def start(self) -> None:
        if not _HAS_PYAUDIO:
            log.warning("[Hotword] pyaudio missing — engine not started")
            return
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="jarvis-hotword",
        )
        self._thread.start()
        log.info(
            "[Hotword] Engine started  rate=%d Hz  frame=%d ms  power_save=%s",
            self.rate, self.frame_ms, self._power_save,
        )

    def stop(self) -> None:
        self._running = False

    # ── Listen loop ───────────────────────────────────────────────────────────

    def _listen_loop(self) -> None:
        import pyaudio  # noqa: PLC0415

        pa    = pyaudio.PyAudio()
        rate  = self.rate
        fms   = self.frame_ms
        chunk = int(rate * fms / 1000)

        # VAD (requires exact 10/20/30 ms frames at 8/16/32 kHz)
        vad = _vad_mod.Vad(2) if _HAS_VAD else None  # aggressiveness 0–3

        clap = ClapDetector()
        oww  = self._oww_model

        # OWW requires exactly 16 kHz × 30 ms = 480 samples per prediction
        _OWW_FRAME = 480
        _oww_buf:  list[bytes] = []

        stream = None
        try:
            stream = pa.open(
                rate=rate,
                channels=_CHANNELS,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=chunk,
            )
            log.info("[Hotword] Mic open — listening…")

            while self._running:
                try:
                    pcm = stream.read(chunk, exception_on_overflow=False)
                except OSError as exc:
                    log.warning("[Hotword] Stream read error: %s", exc)
                    break

                # ── Clap detection (always, even on silent frames) ─────────
                if clap.feed(pcm):
                    log.info("[Hotword] Double-clap!")
                    self._on_wake(WakeEvent.CLAP)
                    continue

                # ── VAD gate: skip non-speech to save CPU ─────────────────
                if vad is not None:
                    try:
                        is_speech = vad.is_speech(pcm, rate)
                    except Exception:
                        is_speech = True
                    if not is_speech:
                        continue

                # ── openWakeWord ──────────────────────────────────────────
                if oww is None:
                    continue

                # Upsample 8 kHz → 16 kHz by repeating each sample
                frame_pcm = pcm
                if rate == _POWERSAVE_RATE and _HAS_NUMPY:
                    arr      = np.frombuffer(pcm, dtype=np.int16)
                    frame_pcm = np.repeat(arr, 2).tobytes()
                elif rate != 16_000:
                    continue  # unsupported rate

                _oww_buf.append(frame_pcm)

                # Accumulate until we have enough for one OWW prediction
                total_samples = sum(len(b) // 2 for b in _oww_buf)
                if total_samples < _OWW_FRAME:
                    continue

                if not _HAS_NUMPY:
                    _oww_buf = []
                    continue

                full = np.concatenate(
                    [np.frombuffer(b, dtype=np.int16) for b in _oww_buf]
                )
                _oww_buf = []

                # Feed in non-overlapping OWW_FRAME windows
                for start in range(0, len(full) - _OWW_FRAME + 1, _OWW_FRAME):
                    frame = full[start : start + _OWW_FRAME]
                    try:
                        preds = oww.predict(frame)
                        for model_name, score in preds.items():
                            if score >= _OWW_SCORE_THRESH:
                                log.info(
                                    "[Hotword] Wake word! model=%s score=%.2f",
                                    model_name, score,
                                )
                                self._on_wake(WakeEvent.HOTWORD)
                    except Exception as exc:
                        log.debug("[Hotword] OWW predict error: %s", exc)

                # Remainder back into buffer
                remainder_start = (len(full) // _OWW_FRAME) * _OWW_FRAME
                if remainder_start < len(full):
                    _oww_buf = [full[remainder_start:].tobytes()]

        except Exception as exc:
            log.error("[Hotword] Fatal listen_loop error: %s", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            pa.terminate()
            log.info("[Hotword] Microphone closed.")
