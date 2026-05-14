"""
generate_boot_sound.py — eDEX-UI / JARVIS style boot sound generator

Layers:
  1. Deep power-up sweep  (80 → 900 Hz exponential chirp)
  2. Digital pulse train  (8 rising beeps — eDEX tick feel)
  3. TRON glitch bursts   (6 micro-tones, 0.9 – 1.4 s)
  4. Warm harmonic pad    (sub bass bed, 1.2 – 3.6 s)
  5. High-freq shimmer    (3800 Hz AM, 1.5 – 3.5 s)
  6. Resolution chord     (A4 + E5 + A5, 2.9 – 4.0 s)
  7. Final click          (crisp termination at ~3.95 s)
  8. Echo simulation      (2 reflections, 72 ms / 144 ms)

Output: assets/boot.wav  (4.0 s · 44100 Hz · 16-bit mono)
Usage : python generate_boot_sound.py
"""

import pathlib
import wave

import numpy as np

SR       = 44_100
DURATION = 4.0


# ── helpers ──────────────────────────────────────────────────────────────────

def _sin(freq: float, t: np.ndarray) -> np.ndarray:
    return np.sin(2 * np.pi * freq * t)


def _env(n: int, attack: float = 0.0, release: float = 0.0) -> np.ndarray:
    e = np.ones(n)
    a = int(attack * SR)
    r = int(release * SR)
    if a: e[:a]  = np.linspace(0, 1, a)
    if r: e[-r:] = np.linspace(1, 0, r)
    return e


def _chirp(f0: float, f1: float, dur: float) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), False)
    k = (f1 / f0) ** (1.0 / dur)
    return np.sin(2 * np.pi * f0 * (k ** t - 1) / np.log(k))


def _place(audio: np.ndarray, sig: np.ndarray,
           start_s: float, amp: float = 1.0) -> None:
    s = int(start_s * SR)
    e = min(s + len(sig), len(audio))
    audio[s:e] += sig[:e - s] * amp


# ── generator ────────────────────────────────────────────────────────────────

def generate(out_path: pathlib.Path, duration: float = DURATION) -> None:
    n     = int(SR * duration)
    audio = np.zeros(n)

    # 1. Deep power-up sweep  ────────────────────────────────────────────────
    sw_dur = 1.8
    sw     = _chirp(80, 900, sw_dur) * _env(int(sw_dur * SR), attack=0.12, release=0.30)
    _place(audio, sw, 0.0, amp=0.30)

    # 2. Digital pulse train  ────────────────────────────────────────────────
    pulse_freqs = [220, 294, 370, 440, 587, 740, 880, 1175]
    pulse_times = np.linspace(0.15, 1.85, len(pulse_freqs))
    pdur        = 0.048
    for freq, pt in zip(pulse_freqs, pulse_times):
        tp = np.linspace(0, pdur, int(pdur * SR), False)
        p  = _sin(freq, tp) * np.exp(-tp * 42) * _env(int(pdur * SR), attack=0.002)
        _place(audio, p, pt, amp=0.32)

    # 3. TRON glitch bursts  ─────────────────────────────────────────────────
    glitch_freqs   = [1320, 1047, 1760, 880, 1568, 1109]
    glitch_offsets = np.linspace(0.0, 0.50, len(glitch_freqs))
    for freq, offset in zip(glitch_freqs, glitch_offsets):
        tp = np.linspace(0, 0.018, int(0.018 * SR), False)
        g  = _sin(freq, tp) * np.exp(-tp * 90)
        _place(audio, g, 0.90 + offset, amp=0.18)

    # 4. Warm harmonic pad  ──────────────────────────────────────────────────
    pad_dur = 2.4
    pad_n   = int(pad_dur * SR)
    tp      = np.linspace(0, pad_dur, pad_n, False)
    pad     = _sin(110, tp) * 0.55 + _sin(220, tp) * 0.30 + _sin(330, tp) * 0.15
    pad    *= _env(pad_n, attack=0.40, release=0.50)
    _place(audio, pad, 1.2, amp=0.10)

    # 5. High-freq shimmer  ──────────────────────────────────────────────────
    sh_dur = 2.0
    sh_n   = int(sh_dur * SR)
    ts     = np.linspace(0, sh_dur, sh_n, False)
    sh     = _sin(3800, ts) * (0.5 + 0.5 * _sin(7.5, ts))
    sh    *= _env(sh_n, attack=0.30, release=0.40)
    _place(audio, sh, 1.5, amp=0.055)

    # 6. Resolution chord  A4 + E5 + A5  ─────────────────────────────────────
    res_dur = 1.05
    res_n   = int(res_dur * SR)
    tr      = np.linspace(0, res_dur, res_n, False)
    res     = _sin(440, tr) * 0.50 + _sin(659, tr) * 0.30 + _sin(880, tr) * 0.20
    res    *= _env(res_n, attack=0.08, release=0.45)
    _place(audio, res, 2.9, amp=0.22)

    # 7. Final click  ────────────────────────────────────────────────────────
    ck_dur = 0.012
    ck_n   = int(ck_dur * SR)
    tc     = np.linspace(0, ck_dur, ck_n, False)
    ck     = _sin(2200, tc) * np.exp(-tc * 280)
    _place(audio, ck, duration - 0.05, amp=0.40)

    # 8. Echo / reverb simulation  ───────────────────────────────────────────
    for delay_ms, decay in [(72, 0.22), (144, 0.10)]:
        d    = int(delay_ms / 1000 * SR)
        echo = np.zeros(n)
        echo[d:] = audio[:-d] * decay
        audio   += echo

    # Master fade-out + normalize  ───────────────────────────────────────────
    fade = int(0.35 * SR)
    audio[-fade:] *= np.linspace(1, 0, fade)

    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio / peak * 0.88   # −1.2 dBFS headroom

    pcm = (audio * 32767).astype(np.int16)

    # Write WAV  ─────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())

    print(f"Generated  →  {out_path}")
    print(f"            {duration:.1f} s · {SR} Hz · 16-bit mono · {out_path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    out = pathlib.Path(__file__).parent / "assets" / "boot.wav"
    generate(out)
