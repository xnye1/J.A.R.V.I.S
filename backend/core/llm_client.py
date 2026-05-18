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


def generate_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str = "",
    max_tokens: int = 2048,
) -> dict:
    """
    Blocking tool-calling completion.
    Returns dict:
      {"content": str, "tool_calls": None}                         — final text answer
      {"content": None, "tool_calls": list, "raw_message": dict}   — wants to call tools
    """
    if _cfg:
        return _oai_generate_with_tools(messages, tools, system, max_tokens)
    return _gemini_generate_with_tools(messages, tools, system, max_tokens)


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


def _build_agent_messages(messages: list[dict], system: str) -> list[dict]:
    """Build proper OpenAI-format messages for tool-calling (preserves tool role)."""
    result = []
    if system:
        result.append({"role": "system", "content": system})
    for m in messages:
        role = m.get("role")
        if role == "user":
            result.append({"role": "user", "content": str(m.get("content", ""))})
        elif role == "assistant":
            raw = m.get("raw_message")
            if isinstance(raw, dict) and raw.get("role") == "assistant":
                result.append(raw)   # includes tool_calls field for proper multi-turn
            else:
                result.append({"role": "assistant", "content": str(m.get("content") or "")})
        elif role == "tool":
            result.append({
                "role":         "tool",
                "tool_call_id": m.get("tool_call_id", ""),
                "content":      str(m.get("content", "")),
            })
    return result


def _oai_generate_with_tools(
    messages: list[dict], tools: list[dict], system: str, max_tokens: int
) -> dict:
    client = _get_openai_client()
    msgs   = _build_agent_messages(messages, system)
    last_exc: Exception | None = None
    delay = 1.0
    for attempt in range(3):
        try:
            resp  = client.chat.completions.create(
                model=LLM_MODEL, messages=msgs, tools=tools,
                tool_choice="auto", max_tokens=max_tokens,
            )
            choice = resp.choices[0]
            msg    = choice.message
            if msg.tool_calls:
                return {
                    "content":    msg.content,
                    "tool_calls": msg.tool_calls,
                    "raw_message": {
                        "role": "assistant", "content": msg.content,
                        "tool_calls": [
                            {"id": tc.id, "type": "function",
                             "function": {"name": tc.function.name,
                                          "arguments": tc.function.arguments}}
                            for tc in msg.tool_calls
                        ],
                    },
                }
            return {"content": msg.content or "", "tool_calls": None}
        except Exception as e:
            last_exc = e
            if _is_rate_limit(e) and attempt < 2:
                time.sleep(delay); delay *= 2
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


# ── Gemini ───────────────────────────────────────────────────────────────────

def _gemini_generate_with_tools(
    messages: list[dict], tools: list[dict], system: str, max_tokens: int
) -> dict:
    """Gemini function calling via google-genai SDK."""
    import json, time as _time
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {"content": _gemini_generate(messages, system, max_tokens), "tool_calls": None}

    client = genai.Client(api_key=API_KEY)

    # Convert OpenAI tool schema → Gemini FunctionDeclaration
    declarations = []
    for t in tools:
        fn    = t.get("function", {})
        props = fn.get("parameters", {}).get("properties", {})
        req   = fn.get("parameters", {}).get("required", [])
        declarations.append(types.FunctionDeclaration(
            name=fn.get("name", ""),
            description=fn.get("description", ""),
            parameters=types.Schema(
                type_=types.Type.OBJECT,
                properties={
                    k: types.Schema(
                        type_=types.Type.STRING,
                        description=v.get("description", ""),
                    )
                    for k, v in props.items()
                },
                required=req,
            ),
        ))
    gem_tools = types.Tool(function_declarations=declarations)

    # Build Gemini Contents from message history
    contents: list = []
    for m in messages:
        role    = m.get("role")
        content = m.get("content", "")
        if role == "user":
            contents.append(types.Content(
                role="user", parts=[types.Part.from_text(str(content))],
            ))
        elif role == "assistant":
            stored = m.get("_gemini_raw")
            if stored is not None:
                contents.append(stored)      # reuse original Gemini Content object
            else:
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(str(content) if content else " ")],
                ))
        elif role == "tool":
            fn_name = m.get("name", m.get("tool_call_id", "tool"))
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(
                    name=fn_name,
                    response={"result": str(content)},
                )],
            ))

    try:
        resp = client.models.generate_content(
            model=LLM_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[gem_tools],
                system_instruction=system or None,
                max_output_tokens=max_tokens,
            ),
        )
    except Exception:
        return {"content": _gemini_generate(messages, system, max_tokens), "tool_calls": None}

    candidate = resp.candidates[0] if resp.candidates else None
    if not candidate or not candidate.content:
        return {"content": getattr(resp, "text", "") or "", "tool_calls": None}

    fn_calls, text_parts = [], []
    for part in (candidate.content.parts or []):
        fc = getattr(part, "function_call", None)
        if fc and getattr(fc, "name", None):
            fn_calls.append((fc.name, dict(fc.args or {})))
        txt = getattr(part, "text", None)
        if txt:
            text_parts.append(txt)

    if fn_calls:
        class _Fn:
            def __init__(self, n, a): self.name = n; self.arguments = a
        class _TC:
            def __init__(self, i, f): self.id = i; self.function = f

        ts = int(_time.time() * 1000)
        fake_tcs = [
            _TC(f"gcall_{name}_{ts+i}", _Fn(name, json.dumps(args)))
            for i, (name, args) in enumerate(fn_calls)
        ]
        return {
            "content":     None,
            "tool_calls":  fake_tcs,
            "raw_message": {
                "role":        "assistant",
                "content":     None,
                "_gemini_raw": candidate.content,  # preserve for next turn
            },
        }

    return {"content": "\n".join(text_parts) or getattr(resp, "text", "") or "", "tool_calls": None}


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
