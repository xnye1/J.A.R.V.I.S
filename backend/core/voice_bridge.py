"""
core/voice_bridge.py — ElevenLabs TTS + smart audio routing.

Usage:
    from core.voice_bridge import speak, handle_arrival
    await speak("Good morning, Sir.", route, broadcast_fn, voice_params)
    await handle_arrival(lat, lon, person="sir", ...)
"""

from __future__ import annotations

import base64
import logging
import os

import httpx

from core.stealth import OutputMode, RouteDecision, classify_location, welcome_home_phrase

log = logging.getLogger("jarvis.voice")

_EL_KEY     = os.getenv("ELEVENLABS_API_KEY", "")
_EL_VOICE   = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
_EL_URL     = f"https://api.elevenlabs.io/v1/text-to-speech/{_EL_VOICE}/stream"
_EL_ENABLED = _EL_KEY.startswith("sk_")


async def synthesize(text: str, params: dict | None = None) -> bytes | None:
    """Call ElevenLabs. Returns MP3 bytes or None if unavailable."""
    if not _EL_ENABLED:
        log.debug("ElevenLabs key absent — TTS skipped.")
        return None

    p = params or {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0}
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            r = await client.post(
                _EL_URL,
                headers={"xi-api-key": _EL_KEY, "Accept": "audio/mpeg",
                         "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_multilingual_v2",
                      "voice_settings": p},
            )
            r.raise_for_status()
            return r.content
        except Exception as exc:
            log.warning("TTS synthesis error: %s", exc)
            return None


async def speak(
    text: str,
    route: RouteDecision,
    broadcast_fn,
    voice_params: dict | None = None,
) -> dict:
    """
    Deliver speech via the correct channel based on route decision.

    Modes:
      SILENT       → haptic signal to remotes
      QUIET        → silent text notification
      VOICE_GENIE  → GiGA Genie TTS event
      VOICE_PHONE  → ElevenLabs audio pushed to phone (base64),
                     falls back to browser TTS text if key missing
    """
    result = {"text": text, "mode": route.mode.value, "reason": route.reason, "tts": False}

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

    # VOICE_PHONE
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


async def handle_arrival(
    lat: float,
    lon: float,
    person: str = "sir",
    route: RouteDecision | None = None,
    broadcast_fn=None,
    voice_params: dict | None = None,
) -> dict:
    """
    Called when phone reports a GPS update.
    If location is 'home' → play welcome phrase via appropriate device.
    """
    location = classify_location(lat, lon)
    result = {"location": location, "phrase": None, "spoken": False}

    if location == "home" and route is not None and broadcast_fn is not None:
        phrase = welcome_home_phrase(person)
        result["phrase"] = phrase
        await speak(phrase, route, broadcast_fn, voice_params)
        result["spoken"] = True

    return result
