"""
system_info.py — Detailed local telemetry via psutil.
Provides CPU, memory, and disk metrics for the HUD TELEMETRY panel.
"""

import psutil
from dataclasses import dataclass, asdict


@dataclass
class DetailedStatus:
    cpu_percent: float
    memory_percent: float
    memory_used_gb: float
    memory_total_gb: float
    disk_free_gb: float
    disk_used_gb: float
    disk_total_gb: float
    disk_percent: float
    battery_percent: float | None
    battery_plugged: bool | None


def get_detailed_status(disk_path: str = "/") -> DetailedStatus:
    cpu  = psutil.cpu_percent(interval=0.3)
    mem  = psutil.virtual_memory()
    bat  = psutil.sensors_battery()

    try:
        disk = psutil.disk_usage(disk_path)
        dfree  = round(disk.free  / 1_073_741_824, 1)
        dused  = round(disk.used  / 1_073_741_824, 1)
        dtotal = round(disk.total / 1_073_741_824, 1)
        dpct   = disk.percent
    except Exception:
        dfree = dused = dtotal = dpct = 0.0

    return DetailedStatus(
        cpu_percent     = round(cpu, 1),
        memory_percent  = round(mem.percent, 1),
        memory_used_gb  = round(mem.used  / 1_073_741_824, 1),
        memory_total_gb = round(mem.total / 1_073_741_824, 1),
        disk_free_gb    = dfree,
        disk_used_gb    = dused,
        disk_total_gb   = dtotal,
        disk_percent    = dpct,
        battery_percent = round(bat.percent, 1) if bat else None,
        battery_plugged = bat.power_plugged      if bat else None,
    )


def to_dict(s: DetailedStatus) -> dict:
    return asdict(s)
