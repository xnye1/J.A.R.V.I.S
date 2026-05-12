"""
services/__init__.py — Dynamic service registry.

Services are registered here and started/stopped collectively during lifespan.
Adding a new service = subclass BaseService + add to REGISTRY.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from services.base_service      import BaseService
from services.memory_service    import MemoryService
from services.dopamine_guard    import DopamineGuard
from services.study_service     import StudyService
from services.productivity_service import ProductivityService
from services.intelligence_service import IntelligenceService
from services.system_service    import SystemService
from services.analysis_service  import AnalysisService

if TYPE_CHECKING:
    from core.dispatcher import HUDDispatcher
    from core.state      import StateManager

log = logging.getLogger("jarvis.registry")


class ServiceRegistry:
    """
    Owns the collection of active services.
    Provides collective start/stop and name-based lookup.
    """

    def __init__(
        self,
        dispatcher: "HUDDispatcher",
        state:      "StateManager",
    ) -> None:
        self._dp  = dispatcher
        self._st  = state
        self._svcs: dict[str, BaseService] = {}
        self._build()

    def _build(self) -> None:
        """Instantiate all registered service classes."""
        classes: list[type[BaseService]] = [
            MemoryService,
            DopamineGuard,
            StudyService,
            ProductivityService,
            IntelligenceService,
            SystemService,
            AnalysisService,
        ]
        for cls in classes:
            svc = cls(self._dp, self._st)
            self._svcs[svc.name] = svc
            log.info("[Registry] Registered service: %s", svc.display_name)

    async def start_all(self) -> None:
        """Start every registered service."""
        for svc in self._svcs.values():
            try:
                await svc.start()
            except Exception as exc:
                log.error("[Registry] Failed to start %s: %s", svc.name, exc)

    async def stop_all(self) -> None:
        """Stop every registered service in reverse registration order."""
        for svc in reversed(list(self._svcs.values())):
            try:
                await svc.stop()
            except Exception as exc:
                log.error("[Registry] Failed to stop %s: %s", svc.name, exc)

    def get(self, name: str) -> BaseService | None:
        return self._svcs.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._svcs.keys())
