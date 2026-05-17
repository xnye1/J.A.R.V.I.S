"""
core/voice_bridge.py — ElevenLabs TTS + smart audio routing.

Voice params are computed continuously from the live Fury Gauge (0-100)
rather than at discrete stage thresholds, giving a smooth emotional arc:

  Gauge 0   → stability=0.75, similarity=0.85, style=0.00  (calm butler)
  Gauge 50  → stability=0.45, similarity=0.90, style=0.50  (stern)
  Gauge 100 → stability=0.15, similarity=0.95, style=1.00  (lockdown)

Explicit voice_params override the live mapping when provided.
"""

from __future__ import annotations

import base64
import logging
import os
import re

import httpx

from core.stealth import OutputMode, RouteDecision, classify_location, welcome_home_phrase

log = logging.getLogger("jarvis.voice")

# ── TTS text sanitiser ────────────────────────────────────────────────────────

_MD_RE = re.compile(
    r'\*{1,3}([^*\n]*)\*{1,3}'   # **bold** / *italic* / ***both***
    r'|`{1,3}[^`]*`{1,3}'         # `code` / ```block```
    r'|#{1,6}\s+'                  # ## headers
    r'|\[([^\]]*)\]\([^)]*\)'      # [link text](url) → keep text
    r'|[-*+]\s+'                   # - list items
    r'|>\s+'                       # > blockquote
    r'|_{1,2}([^_\n]*)_{1,2}'     # _italic_ / __bold__
    r'|\~{2}([^\~]*)~{2}',         # ~~strikethrough~~
)
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U0000FE00-\U0000FE0F"
    "\U00010000-\U0010FFFF]+",
    flags=re.UNICODE,
)


def clean_for_tts(text: str) -> str:
    """Strip all markdown formatting and emoji so TTS reads cleanly."""
    text = _MD_RE.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or m.group(4) or '', text)
    text = _EMOJI_RE.sub('', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

_EL_KEY     = os.getenv("ELEVENLABS_API_KEY", "")
_EL_VOICE   = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
_EL_URL     = f"https://api.elevenlabs.io/v1/text-to-speech/{_EL_VOICE}/stream"
_EL_ENABLED = bool(_EL_KEY)


# ── Continuous voice mapping ──────────────────────────────────────────────────

def gauge_to_voice_params(gauge: float) -> dict:
    """
    Linear interpolation of ElevenLabs voice params from anger gauge 0-100.
    Style uses a mild exponential curve so it ramps up faster at low gauges.
    """
    t = max(0.0, min(1.0, gauge / 100.0))
    return {
        "stability":        round(0.75 - t * 0.60, 2),   # 0.75 → 0.15
        "similarity_boost": round(0.85 + t * 0.10, 2),   # 0.85 → 0.95
        "style":            round(t ** 0.75, 2),           # 0.00 → 1.00 (accelerated)
    }


# ── ElevenLabs synthesis ──────────────────────────────────────────────────────

async def synthesize(text: str, params: dict | None = None) -> bytes | None:
    """
    Synthesize speech. Returns MP3 bytes or None on failure.
    Priority: ElevenLabs (quality adaptive voice) → gTTS (free Korean fallback).
    Text is auto-sanitised (markdown + emoji stripped) before synthesis.
    """
    text = clean_for_tts(text)
    if not text:
        return None
    if _EL_ENABLED:
        audio = await _el_synthesize(text, params)
        if audio:
            return audio

    # Fallback: gTTS (no API key needed)
    return await _gtts_synthesize(text)


async def _el_synthesize(text: str, params: dict | None = None) -> bytes | None:
    """ElevenLabs TTS — returns bytes or None."""
    if params is None:
        try:
            from core.anger_engine import anger as _anger
            params = gauge_to_voice_params(_anger.gauge)
        except Exception:
            params = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0}

    async with httpx.AsyncClient(timeout=25) as client:
        try:
            r = await client.post(
                _EL_URL,
                headers={"xi-api-key": _EL_KEY, "Accept": "audio/mpeg",
                         "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_multilingual_v2",
                      "voice_settings": params},
            )
            r.raise_for_status()
            return r.content
        except Exception as exc:
            log.warning("ElevenLabs TTS error: %s", exc)
            return None


async def _gtts_synthesize(text: str) -> bytes | None:
    """gTTS fallback — free, no API key, Korean only."""
    import asyncio, io
    def _synth():
        try:
            from gtts import gTTS
            buf = io.BytesIO()
            gTTS(text=text, lang="ko", slow=False).write_to_fp(buf)
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.debug("gTTS error: %s", exc)
            return None
    return await asyncio.to_thread(_synth)


# ── Routing + delivery ────────────────────────────────────────────────────────

async def speak(
    text: str,
    route: RouteDecision,
    broadcast_fn,
    voice_params: dict | None = None,
) -> dict:
    """
    Deliver speech via the correct channel based on route decision.

    Modes:
      STANDBY      → no output (dormitory isolation)
      SILENT       → haptic signal to remotes
      QUIET        → silent text notification
      VOICE_GENIE  → GiGA Genie TTS event
      VOICE_PHONE  → ElevenLabs audio pushed to phone (base64),
                     falls back to browser TTS text if key missing
    """
    result = {"text": text, "mode": route.mode.value, "reason": route.reason, "tts": False}

    if route.mode == OutputMode.STANDBY:
        return result   # complete silence — no haptic, no notification

    if route.mode == OutputMode.SILENT:
        await broadcast_fn({"type": "haptic", "pattern": "warning", "reason": route.reason})
        return result

    if route.mode == OutputMode.QUIET:
        await broadcast_fn({"type": "notification", "text": text, "silent": True})
        return result

    if route.mode == OutputMode.VOICE_GENIE:
        await broadcast_fn({"type": "gigagenie_tts", "text": text})
        result["tts"] = True
        return result

    # VOICE_PHONE — use continuous gauge mapping unless override provided
    audio = await synthesize(text, voice_params)
    if audio:
        await broadcast_fn({
            "type":      "tts_audio",
            "text":      text,
            "audio_b64": base64.b64encode(audio).decode(),
        })
        result["tts"] = True
    else:
        await broadcast_fn({"type": "tts_text_fallback", "text": text})

    return result


# ── Welcome Home ─────────────────────────────────────────────────────────────

async def handle_arrival(
    lat: float,
    lon: float,
    person: str = "sir",
    route: RouteDecision | None = None,
    broadcast_fn=None,
    voice_params: dict | None = None,
) -> dict:
    """GPS home arrival → welcome phrase via appropriate device."""
    location = classify_location(lat, lon)
    result = {"location": location, "phrase": None, "spoken": False}

    if location == "home" and route is not None and broadcast_fn is not None:
        phrase = welcome_home_phrase(person)
        result["phrase"] = phrase
        await speak(phrase, route, broadcast_fn, voice_params)
        result["spoken"] = True

    return result
