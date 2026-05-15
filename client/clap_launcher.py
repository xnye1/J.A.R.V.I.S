"""
client/clap_launcher.py — Double-clap → launch JARVIS overlay

Two claps within 1.2 s → subprocess.Popen(python -m client.main)
Runs at near-zero CPU: 16 kHz mono, 20 ms chunks, simple RMS threshold.

Usage:
  python -m client.clap_launcher          # foreground (shows logs)
  python -m client.clap_launcher --debug  # prints live RMS to tune threshold
  pythonw -m client.clap_launcher         # silent background (Windows)

Auto-start on login (Windows Task Scheduler):
  Action : pythonw.exe -m client.clap_launcher
  Start in: C:\\Users\\yejun\\OneDrive\\Desktop\\Projects(Jarvis)
  Trigger : At log on

Background log: %USERPROFILE%\\.jarvis\\clap.log
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib
import subprocess
import sys
import time

# ── File log so pythonw (no console) can be debugged ──────────────────────────
_LOG_DIR = pathlib.Path.home() / ".jarvis"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "clap.log"

_fh = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8"
)
_fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s",
                                    datefmt="%H:%M:%S"))
logging.getLogger().addHandler(_fh)

log = logging.getLogger("jarvis.clap")

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
JARVIS_CMD   = [sys.executable, "-m", "client.main"]

# ── Tuning ────────────────────────────────────────────────────────────────────
SAMPLE_RATE    = 16_000   # Hz  — low sample rate keeps CPU near 0 %
CHUNK_MS       = 20       # ms per analysis window
CHUNK_FRAMES   = int(SAMPLE_RATE * CHUNK_MS / 1000)
CLAP_THRESHOLD = 0.15     # RMS threshold (0-1). Lower = more sensitive.
MIN_GAP_S      = 0.06     # debounce: ignore re-trigger within this window
MAX_GAP_S      = 1.20     # double-clap must land within this window
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


def _run_stream(debug: bool) -> None:
    """Open one InputStream session. Raises on failure (caller retries)."""
    import sounddevice as sd

    last_clap_t   = 0.0
    prev_clap_t   = 0.0
    last_launch_t = 0.0
    above          = False
    _debug_last_print = 0.0

    log.info("[Clap] Stream open  (threshold=%.2f, window=%.1fs, device=default)",
             CLAP_THRESHOLD, MAX_GAP_S)
    if debug:
        print(f"\n[DEBUG] threshold={CLAP_THRESHOLD:.2f}  window={MAX_GAP_S:.1f}s")
        print("[DEBUG] 박수를 쳐보세요. RMS 값이 실시간으로 출력됩니다.\n")

    def _cb(indata, frames, t, status):
        nonlocal last_clap_t, prev_clap_t, last_launch_t, above, _debug_last_print
        try:
            if status:
                log.warning("[Clap] stream status: %s", status)
            now = time.monotonic()
            rms = _rms(indata[:, 0])

            if debug and rms > 0.01:
                if now - _debug_last_print > 0.05:
                    bar_len = int(rms * 60)
                    bar = "█" * min(bar_len, 60)
                    mark = " ← CLAP!" if rms >= CLAP_THRESHOLD else ""
                    print(f"\r  rms={rms:.3f}  |{bar:<60}|{mark}    ", end="", flush=True)
                    _debug_last_print = now

            if rms >= CLAP_THRESHOLD:
                if not above:
                    above = True
                    gap   = now - last_clap_t
                    if gap >= MIN_GAP_S:
                        prev_clap_t = last_clap_t
                        last_clap_t = now
                        inter = now - prev_clap_t
                        if debug:
                            print(f"\n[DEBUG] 박수 감지! inter={inter:.3f}s  "
                                  f"(window={MAX_GAP_S:.1f}s)")
                        if 0 < inter <= MAX_GAP_S:
                            if (now - last_launch_t) > COOLDOWN_S:
                                last_launch_t = now
                                if debug:
                                    print("[DEBUG] 🎯 더블-클랩 → JARVIS 실행!\n")
                                _launch()
            else:
                above = False
        except Exception as exc:                    # callback runs in C thread
            log.error("[Clap] callback error: %s", exc)

    with sd.InputStream(
        samplerate = SAMPLE_RATE,
        channels   = 1,
        blocksize  = CHUNK_FRAMES,
        dtype      = "float32",
        callback   = _cb,
    ):
        while True:
            time.sleep(0.5)


def run(debug: bool = False) -> None:
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        sys.exit("sounddevice not installed: pip install sounddevice")

    retry_delay = 5
    while True:
        try:
            _run_stream(debug)
        except KeyboardInterrupt:
            log.info("[Clap] Stopped.")
            break
        except Exception as exc:
            log.error("[Clap] Stream error: %s — restarting in %ds", exc, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # cap at 1 min
        else:
            retry_delay = 5  # reset on clean exit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")
    _debug = "--debug" in sys.argv
    run(debug=_debug)
