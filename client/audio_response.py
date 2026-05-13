"""
client/audio_response.py — Pre-loaded "Yes, Sir" audio for J.A.R.V.I.S.

AudioResponse.preload()   — loads WAV into RAM once at startup
AudioResponse.play()      — plays preloaded WAV with <5 ms latency
AudioResponse.generate()  — creates ~/.jarvis/yes_sir.wav via pyttsx3 or chime fallback

simpleaudio provides zero-latency memory-mapped WAV playback.
Falls back to subprocess aplay/paplay/afplay if simpleaudio is unavailable.
"""

from __future__ import annotations

import logging
import math
import struct
import subprocess
import wave
from pathlib import Path
from typing import Union

log = logging.getLogger("jarvis.audio_response")

_DEFAULT_WAV: Path = Path.home() / ".jarvis" / "yes_sir.wav"

try:
    import simpleaudio as sa
    _HAS_SA = True
except ImportError:
    sa = None  # type: ignore[assignment]
    _HAS_SA = False
    log.warning("[Audio] simpleaudio not installed — using subprocess fallback")


class AudioResponse:
    """
    Pre-loads a WAV file into memory for instant, non-blocking playback.
    Thread-safe: play() may be called from any thread.
    """

    def __init__(self, wav_path: Union[str, Path] = _DEFAULT_WAV) -> None:
        self._path     = Path(wav_path)
        self._wave_obj: object | None = None   # sa.WaveObject when preloaded

    # ── Public ────────────────────────────────────────────────────────────────

    def preload(self) -> None:
        """Load WAV into RAM. Generates the file if it doesn't exist yet."""
        if not self._path.exists():
            self.generate(self._path)

        if _HAS_SA and sa is not None:
            try:
                self._wave_obj = sa.WaveObject.from_wave_file(str(self._path))
                log.info("[Audio] Preloaded %s via simpleaudio", self._path)
            except Exception as exc:
                log.warning("[Audio] simpleaudio preload failed: %s", exc)
                self._wave_obj = None
        else:
            log.info("[Audio] Will use subprocess fallback for playback")

    def play(self) -> None:
        """
        Start playback immediately and return without waiting.
        Uses the preloaded WaveObject if available; falls back to subprocess.
        """
        if _HAS_SA and self._wave_obj is not None:
            try:
                self._wave_obj.play()  # type: ignore[union-attr]
                return
            except Exception as exc:
                log.warning("[Audio] simpleaudio play failed: %s", exc)

        # Subprocess fallback — try common audio players in priority order
        if self._path.exists():
            for player in ("aplay", "paplay", "afplay", "play"):
                try:
                    subprocess.Popen(
                        [player, str(self._path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except FileNotFoundError:
                    continue
            log.warning("[Audio] No audio player found (aplay/paplay/afplay/play)")

    # ── WAV generation ────────────────────────────────────────────────────────

    @staticmethod
    def generate(path: Path = _DEFAULT_WAV) -> None:
        """
        Write a 'Yes, Sir.' WAV to `path`.
        Tries pyttsx3 TTS first; falls back to a synthesised C-E-G chime.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        # Attempt pyttsx3 TTS
        try:
            import pyttsx3  # type: ignore[import]
            eng = pyttsx3.init()
            voices = eng.getProperty("voices") or []
            for v in voices:
                name = (v.name or "").lower()
                if "female" in name or "zira" in name or "samantha" in name:
                    eng.setProperty("voice", v.id)
                    break
            eng.setProperty("rate",   165)
            eng.setProperty("volume", 0.90)
            eng.save_to_file("Yes, Sir.", str(path))
            eng.runAndWait()
            if path.exists() and path.stat().st_size > 200:
                log.info("[Audio] TTS generated %s", path)
                return
        except Exception as exc:
            log.debug("[Audio] pyttsx3 failed (%s) — writing chime", exc)

        # Fallback: C5–E5–G5 ascending chime
        _write_chime_wav(path)
        log.info("[Audio] Chime fallback written to %s", path)


def _write_chime_wav(path: Path) -> None:
    """Write a short 3-note ascending chime WAV (no dependencies)."""
    rate      = 22_050
    amp       = 14_000
    notes     = [523, 659, 784]   # C5 E5 G5
    note_dur  = 0.11              # seconds per note
    gap_dur   = 0.025             # silence between notes

    frames: list[bytes] = []
    for freq in notes:
        n = int(rate * note_dur)
        attack_end = int(n * 0.12)
        decay_start = int(n * 0.65)
        for i in range(n):
            # Envelope: linear attack → sustain → linear decay
            if i < attack_end:
                env = i / attack_end
            elif i >= decay_start:
                env = max(0.0, 1.0 - (i - decay_start) / (n - decay_start))
            else:
                env = 1.0
            val = int(amp * env * math.sin(2 * math.pi * freq * i / rate))
            frames.append(struct.pack("<h", max(-32767, min(32767, val))))
        # Silence gap
        silence = int(rate * gap_dur)
        frames.extend(struct.pack("<h", 0) for _ in range(silence))

    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))
