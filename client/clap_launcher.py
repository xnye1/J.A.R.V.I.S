"""
client/clap_launcher.py — Double-clap → launch JARVIS overlay

Two claps within 0.7 s → subprocess.Popen(python -m client.main)
Runs at near-zero CPU: 16 kHz mono, 20 ms chunks, simple RMS threshold.

Usage:
  python -m client.clap_launcher          # foreground (shows logs)
  pythonw -m client.clap_launcher         # silent background (Windows)

Auto-start on login (Windows Task Scheduler):
  Action : pythonw.exe -m client.clap_launcher
  Start in: C:\\Users\\yejun\\OneDrive\\Desktop\\Projects(Jarvis)
  Trigger : At log on
"""
from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
import time

log = logging.getLogger("jarvis.clap")

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
JARVIS_CMD   = [sys.executable, "-m", "client.main"]

# ── Tuning ────────────────────────────────────────────────────────────────────
SAMPLE_RATE    = 16_000   # Hz  — low sample rate keeps CPU near 0 %
CHUNK_MS       = 20       # ms per analysis window
CHUNK_FRAMES   = int(SAMPLE_RATE * CHUNK_MS / 1000)
CLAP_THRESHOLD = 0.30     # RMS threshold (0-1). Lower = more sensitive.
MIN_GAP_S      = 0.08     # debounce: ignore re-trigger within this window
MAX_GAP_S      = 0.70     # double-clap must land within this window
COOLDOWN_S     = 3.0      # ignore further claps after a launch


def _rms(chunk) -> float:
    import numpy as np
    return float(np.sqrt(np.mean(chunk.astype("float32") ** 2)))


def _is_already_running() -> bool:
    """True if 'python -m client.main' process is already alive."""
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
                if "client.main" in cmd:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass
    return False


def _launch() -> None:
    if _is_already_running():
        log.info("[Clap] JARVIS already running — skipped")
        return
    log.info("[Clap] 👊👊 Double-clap → launching JARVIS")
    kwargs: dict = {"cwd": str(PROJECT_ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(JARVIS_CMD, **kwargs)


def run() -> None:
    try:
        import sounddevice as sd
    except ImportError:
        sys.exit("sounddevice not installed: pip install sounddevice")

    last_clap_t   = 0.0
    prev_clap_t   = 0.0
    last_launch_t = 0.0
    above          = False   # were we above threshold last frame?

    log.info("[Clap] Listening for double-clap  (threshold=%.2f, window=%.1fs)",
             CLAP_THRESHOLD, MAX_GAP_S)

    def _cb(indata, frames, t, status):
        nonlocal last_clap_t, prev_clap_t, last_launch_t, above
        now = time.monotonic()
        rms = _rms(indata[:, 0])

        if rms >= CLAP_THRESHOLD:
            if not above:                           # rising edge = new clap
                above     = True
                gap       = now - last_clap_t
                if gap >= MIN_GAP_S:                # debounce
                    prev_clap_t = last_clap_t
                    last_clap_t = now
                    inter = now - prev_clap_t
                    if 0 < inter <= MAX_GAP_S:
                        if (now - last_launch_t) > COOLDOWN_S:
                            last_launch_t = now
                            _launch()
        else:
            above = False

    with sd.InputStream(
        samplerate = SAMPLE_RATE,
        channels   = 1,
        blocksize  = CHUNK_FRAMES,
        dtype      = "float32",
        callback   = _cb,
    ):
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("[Clap] Stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")
    run()
