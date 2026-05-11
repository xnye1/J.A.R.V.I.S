"""
Redis-backed conversation memory.
Falls back to in-memory list when Redis is unavailable (local dev without Docker).
"""

import json
import os
from typing import Any

_REDIS_URL = os.getenv("REDIS_URL", "")
_TTL = 60 * 60 * 24  # 24-hour conversation window

try:
    import redis
    _client = redis.from_url(_REDIS_URL, decode_responses=True) if _REDIS_URL else None
    if _client:
        _client.ping()
except Exception:
    _client = None


def _key(session_id: str) -> str:
    return f"jarvis:history:{session_id}"


def load(session_id: str) -> list[dict[str, Any]]:
    if _client is None:
        return []
    raw = _client.get(_key(session_id))
    return json.loads(raw) if raw else []


def save(session_id: str, history: list[dict[str, Any]]) -> None:
    if _client is None:
        return
    _client.setex(_key(session_id), _TTL, json.dumps(history))


def clear(session_id: str) -> None:
    if _client:
        _client.delete(_key(session_id))


def is_available() -> bool:
    return _client is not None
