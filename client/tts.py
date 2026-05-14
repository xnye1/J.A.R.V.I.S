"""
client/tts.py — JARVIS TTS engine

Priority: ElevenLabs (ELEVENLABS_API_KEY set) → gTTS British-accent fallback.
Returns raw MP3 bytes; caller handles Qt audio playback.

Environment variables:
  ELEVENLABS_API_KEY   — activates ElevenLabs (leave unset to use gTTS)
  ELEVENLABS_VOICE_ID  — default: JBFqnCBsd6RMkjVDRZzb (Brian · EN-US deep voice)
  ELEVENLABS_MODEL     — default: eleven_turbo_v2_5   (low-latency ~200 ms)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional

log = logging.getLogger("jarvis.tts")

_EL_KEY   = os.getenv("ELEVENLABS_API_KEY",  "")
_EL_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
_EL_MODEL = os.getenv("ELEVENLABS_MODEL",    "eleven_turbo_v2_5")


async def synthesize(text: str) -> Optional[bytes]:
    """Return MP3 bytes for text. ElevenLabs first, gTTS fallback, None on failure."""
    text = text.strip()
    if not text:
        return None
    if _EL_KEY:
        data = await _elevenlabs(text)
        if data is not None:
            return data
    return await _gtts(text)


async def _elevenlabs(text: str) -> Optional[bytes]:
    payload = json.dumps({
        "text": text,
        "model_id": _EL_MODEL,
        "voice_settings": {
            "stability":        0.62,
            "similarity_boost": 0.85,
            "style":            0.12,
        },
    }).encode()

    def _do() -> bytes:
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{_EL_VOICE}",
            data    = payload,
            headers = {
                "xi-api-key":   _EL_KEY,
                "Content-Type": "application/json",
                "Accept":       "audio/mpeg",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _do)
    except Exception as exc:
        log.warning("[TTS] ElevenLabs: %s", exc)
        return None


async def _gtts(text: str) -> Optional[bytes]:
    def _do() -> bytes:
        from gtts import gTTS  # type: ignore[import-untyped]
        buf = io.BytesIO()
        gTTS(text=text, lang="en", tld="co.uk").write_to_fp(buf)  # British accent
        return buf.getvalue()

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _do)
    except Exception as exc:
        log.warning("[TTS] gTTS: %s", exc)
        return None


def estimate_ms(text: str) -> int:
    """Generous duration estimate for UI animation before actual audio is ready.
    ~150 wpm average, 400 ms/word, minimum 1.5 s."""
    return max(1500, len(text.split()) * 400)
