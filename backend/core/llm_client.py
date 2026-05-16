"""
core/llm_client.py — Unified LLM provider client.

Provider is selected via LLM_PROVIDER env var:
  groq     → Groq Cloud (OpenAI-compatible, ultra-low latency)  [default]
  together → Together AI (OpenAI-compatible)
  gemini   → Google Gemini (legacy)

Set the matching API key and optionally override LLM_MODEL.
"""

from __future__ import annotations

import os
import time
from typing import Iterator

# ── Provider registry ─────────────────────────────────────────────────────────

_PROVIDERS: dict[str, dict] = {
    "groq": {
        "base_url":      "https://api.groq.com/openai/v1",
        "api_key_env":   "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "together": {
        "base_url":      "https://api.together.xyz/v1",
        "api_key_env":   "TOGETHER_API_KEY",
        "default_model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    },
}

PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()
_cfg     = _PROVIDERS.get(PROVIDER)

if _cfg:
    API_KEY   = os.getenv(_cfg["api_key_env"], "")
    BASE_URL  = _cfg["base_url"]
    LLM_MODEL = os.getenv("LLM_MODEL", _cfg["default_model"])
else:
    # Gemini legacy path
    API_KEY   = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    BASE_URL  = ""
    LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")

SIMULATION_MODE = not API_KEY

_oai_client = None


def _get_openai_client():
    global _oai_client
    if _oai_client is None:
        from openai import OpenAI
        _oai_client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _oai_client


# ── Public API ────────────────────────────────────────────────────────────────

def generate(messages: list[dict], system: str = "", max_tokens: int = 1024) -> str:
    """Blocking completion — returns full reply text."""
    if _cfg:
        return _oai_generate(messages, system, max_tokens)
    return _gemini_generate(messages, system, max_tokens)


def stream(
    messages: list[dict], system: str = "", max_tokens: int = 1024
) -> Iterator[str]:
    """Streaming completion — yields text chunks as they arrive."""
    if _cfg:
        yield from _oai_stream(messages, system, max_tokens)
    else:
        yield _gemini_generate(messages, system, max_tokens)


# ── OpenAI-compatible (Groq / Together AI) ────────────────────────────────────

def _build_messages(messages: list[dict], system: str) -> list[dict]:
    result = []
    if system:
        result.append({"role": "system", "content": system})
    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        result.append({"role": role, "content": m["content"]})
    return result


def _oai_generate(messages: list[dict], system: str, max_tokens: int) -> str:
    client = _get_openai_client()
    msgs   = _build_messages(messages, system)
    last_exc: Exception | None = None
    delay = 1.0
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL, messages=msgs, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            last_exc = e
            if _is_rate_limit(e) and attempt < 2:
                time.sleep(delay)
                delay *= 2
            else:
                break
    raise last_exc  # type: ignore[misc]


def _oai_stream(
    messages: list[dict], system: str, max_tokens: int
) -> Iterator[str]:
    client = _get_openai_client()
    msgs   = _build_messages(messages, system)
    resp = client.chat.completions.create(
        model=LLM_MODEL, messages=msgs, max_tokens=max_tokens, stream=True,
    )
    for chunk in resp:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ── Gemini legacy ─────────────────────────────────────────────────────────────

def _gemini_generate(messages: list[dict], system: str, max_tokens: int) -> str:
    from google import genai
    from google.genai import types

    client   = genai.Client(api_key=API_KEY)
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))

    resp = client.models.generate_content(
        model=LLM_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=max_tokens,
        ),
    )
    return resp.text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_rate_limit(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("429", "rate_limit", "resource_exhausted", "quota"))
