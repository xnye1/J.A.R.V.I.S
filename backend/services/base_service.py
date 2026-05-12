"""
services/base_service.py — Abstract base for all JARVIS service modules.

Every service must subclass BaseService and implement start/stop/name.
The service registry in __init__.py discovers them by this interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.dispatcher import HUDDispatcher
    from core.state import StateManager

log = logging.getLogger("jarvis.service")


class BaseService(ABC):
    """
    Lifecycle contract for all JARVIS services.

    Subclasses receive the shared dispatcher and state at construction
    so they can emit HUD notifications and read/write global state without
    importing module-level singletons directly (enables testing).
    """

    def __init__(
        self,
        dispatcher: "HUDDispatcher",
        state:      "StateManager",
    ) -> None:
        self._dispatcher = dispatcher
        self._state      = state
        self._active     = False

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine-readable service identifier (e.g. 'dopamine_guard')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in HUD service list."""
        ...

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Called once during application lifespan startup."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Called once during application lifespan shutdown."""
        ...

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _emit(self, payload: dict, priority: int = 3) -> None:
        """Convenience wrapper — services call this instead of dispatcher directly."""
        await self._dispatcher.emit(payload, priority)

    def _log(self, msg: str, level: str = "info") -> None:
        getattr(log, level)("[%s] %s", self.name, msg)

    @property
    def is_active(self) -> bool:
        return self._active

    def _mark_online(self) -> None:
        self._active = True
        self._state.register_service(self.name)
        self._log("online.")

    def _mark_offline(self) -> None:
        self._active = False
        self._state.unregister_service(self.name)
        self._log("offline.")
