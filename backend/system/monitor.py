"""
Proactive Engine — polls system metrics and fires alerts when thresholds are breached.
"""

import asyncio
import os
import psutil
from dataclasses import dataclass
from typing import Callable, Awaitable
from dotenv import load_dotenv

load_dotenv()

BATTERY_THRESHOLD = int(os.getenv("BATTERY_WARN_THRESHOLD", 20))
CPU_THRESHOLD = int(os.getenv("CPU_WARN_THRESHOLD", 85))
MEMORY_THRESHOLD = int(os.getenv("MEMORY_WARN_THRESHOLD", 90))
POLL_INTERVAL = int(os.getenv("PROACTIVE_POLL_INTERVAL", 30))


@dataclass
class SystemStatus:
    battery_percent: float | None
    battery_plugged: bool | None
    cpu_percent: float
    memory_percent: float
    alerts: list[str]


AlertCallback = Callable[[str, SystemStatus], Awaitable[None]]


def _get_status() -> SystemStatus:
    battery = psutil.sensors_battery()
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent

    alerts: list[str] = []

    if battery is not None:
        if battery.percent <= BATTERY_THRESHOLD and not battery.power_plugged:
            alerts.append(
                f"Battery critically low at {battery.percent:.0f}%. "
                f"Please connect the power adapter immediately, Sir."
            )
    if cpu >= CPU_THRESHOLD:
        alerts.append(
            f"CPU utilisation has spiked to {cpu:.0f}%. "
            f"Shall I investigate the offending processes, Sir?"
        )
    if mem >= MEMORY_THRESHOLD:
        alerts.append(
            f"Memory consumption is at {mem:.0f}%. "
            f"You may wish to close some applications, Sir."
        )

    return SystemStatus(
        battery_percent=battery.percent if battery else None,
        battery_plugged=battery.power_plugged if battery else None,
        cpu_percent=cpu,
        memory_percent=mem,
        alerts=alerts,
    )


def get_current_status() -> SystemStatus:
    return _get_status()


class ProactiveEngine:
    """Runs in the background; calls `on_alert` whenever a threshold is breached."""

    def __init__(self, on_alert: AlertCallback):
        self.on_alert = on_alert
        self._running = False
        self._seen_alerts: set[str] = set()

    async def start(self):
        self._running = True
        while self._running:
            status = _get_status()
            for alert_text in status.alerts:
                # Deduplicate: don't repeat the same alert until the condition clears
                key = alert_text[:40]
                if key not in self._seen_alerts:
                    self._seen_alerts.add(key)
                    await self.on_alert(alert_text, status)
            # Clear dedup cache for alerts that are no longer firing
            current_keys = {a[:40] for a in status.alerts}
            self._seen_alerts &= current_keys

            await asyncio.sleep(POLL_INTERVAL)

    def stop(self):
        self._running = False
