"""
services/memory_service.py — Long-term semantic memory service.

Interface is Vector-DB-ready; the default backend is an in-process dict.
Swap backends by setting STATE.memory_backend and installing the driver:

  "local"   → Python dict (default, zero dependencies)
  "chroma"  → ChromaDB   (pip install chromadb)
  "pinecone" → Pinecone  (pip install pinecone-client) — needs API key

The public API is intentionally kept narrow so swapping backends
requires only changing the adapter class, not the callers.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.dispatcher import Priority
from services.base_service import BaseService

log = logging.getLogger("jarvis.memory")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    key:       str
    content:   str
    tags:      list[str]    = field(default_factory=list)
    created_at: float       = field(default_factory=time.time)
    access_count: int       = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key":          self.key,
            "content":      self.content,
            "tags":         self.tags,
            "created_at":   self.created_at,
            "access_count": self.access_count,
        }


# ── Backend adapters ──────────────────────────────────────────────────────────

class _LocalAdapter:
    """In-process dict adapter — no external dependencies."""

    def __init__(self) -> None:
        self._store: dict[str, MemoryEntry] = {}

    def put(self, key: str, content: str, tags: list[str]) -> MemoryEntry:
        entry = MemoryEntry(key=key, content=content, tags=tags)
        self._store[key] = entry
        return entry

    def get(self, key: str) -> MemoryEntry | None:
        entry = self._store.get(key)
        if entry:
            entry.access_count += 1
        return entry

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """Naive keyword search — replaced by vector similarity in real backends."""
        q = query.lower()
        scored: list[tuple[int, MemoryEntry]] = []
        for entry in self._store.values():
            score = entry.content.lower().count(q)
            score += sum(q in tag for tag in entry.tags) * 2
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:limit]]

    def all_keys(self) -> list[str]:
        return list(self._store.keys())

    def count(self) -> int:
        return len(self._store)


class _ChromaAdapter:
    """ChromaDB stub — raises ImportError if chromadb is not installed."""

    def __init__(self) -> None:
        import chromadb  # type: ignore[import]
        client      = chromadb.Client()
        self._coll  = client.get_or_create_collection("jarvis_memory")

    def put(self, key: str, content: str, tags: list[str]) -> MemoryEntry:
        self._coll.upsert(
            ids=[key],
            documents=[content],
            metadatas=[{"tags": ",".join(tags)}],
        )
        return MemoryEntry(key=key, content=content, tags=tags)

    def get(self, key: str) -> MemoryEntry | None:
        res = self._coll.get(ids=[key])
        if not res["ids"]:
            return None
        meta = res["metadatas"][0]
        return MemoryEntry(
            key=key,
            content=res["documents"][0],
            tags=meta.get("tags", "").split(","),
        )

    def delete(self, key: str) -> bool:
        self._coll.delete(ids=[key])
        return True

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        res = self._coll.query(query_texts=[query], n_results=limit)
        entries = []
        for i, doc_id in enumerate(res["ids"][0]):
            meta = res["metadatas"][0][i]
            entries.append(MemoryEntry(
                key=doc_id,
                content=res["documents"][0][i],
                tags=meta.get("tags", "").split(","),
            ))
        return entries

    def all_keys(self) -> list[str]:
        return self._coll.get()["ids"]

    def count(self) -> int:
        return self._coll.count()


# ── Service ───────────────────────────────────────────────────────────────────

class MemoryService(BaseService):
    """
    Long-term semantic memory with pluggable vector backends.

    Provides remember / recall / forget / search API callable from
    any other service or route handler.
    """

    @property
    def name(self) -> str:
        return "memory_service"

    @property
    def display_name(self) -> str:
        return "Memory Service (Vector-DB Ready)"

    async def start(self) -> None:
        backend = self._state.memory_backend
        self._adapter = self._load_adapter(backend)
        self._mark_online()
        await self._emit(
            {"type": "service_online", "service": self.name, "backend": backend},
            Priority.LOW,
        )

    async def stop(self) -> None:
        self._mark_offline()

    # ── Public API ────────────────────────────────────────────────────────────

    def remember(self, content: str, key: str | None = None, tags: list[str] | None = None) -> MemoryEntry:
        """
        Store a memory fragment.

        Parameters
        ----------
        content : The text to remember.
        key     : Optional stable ID; auto-generated from content hash if omitted.
        tags    : Searchable labels (e.g. ["finance", "KOSPI"]).
        """
        k = key or hashlib.sha1(content.encode()).hexdigest()[:12]
        return self._adapter.put(k, content, tags or [])

    def recall(self, key: str) -> MemoryEntry | None:
        """Retrieve an exact entry by key."""
        return self._adapter.get(key)

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """
        Semantic search — vector similarity in Chroma/Pinecone backends,
        keyword fallback in local adapter.
        """
        return self._adapter.search(query, limit)

    def forget(self, key: str) -> bool:
        """Delete a memory entry by key. Returns True if it existed."""
        return self._adapter.delete(key)

    def stats(self) -> dict[str, Any]:
        return {
            "backend":  self._state.memory_backend,
            "count":    self._adapter.count(),
            "all_keys": self._adapter.all_keys(),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _load_adapter(backend: str) -> _LocalAdapter | _ChromaAdapter:
        if backend == "chroma":
            try:
                return _ChromaAdapter()
            except Exception as exc:
                log.warning("ChromaDB unavailable (%s) — falling back to local.", exc)
        return _LocalAdapter()
