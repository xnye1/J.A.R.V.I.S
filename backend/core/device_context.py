"""
core/device_context.py — Live device context store for LLM prompt injection.

Phone and laptop snapshots are stored here. build_context_block() produces
a compact text block that is prepended to every LLM call so JARVIS can
answer questions like "배터리 얼마야?", "홍길동 문자 답장 뭐라 해?", etc.

Data sources:
  Phone  — POST /sync/phone  (iOS Shortcut / Tasker)
           WS remote_status  (remote.html battery)
  Laptop — _status_broadcaster auto-feeds every 5 s
           POST /sync/laptop (manual / script)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))


def _kst_now() -> str:
    return datetime.now(_KST).strftime("%H:%M")


class DeviceContext:
    def __init__(self) -> None:
        self._phone:     dict  = {}
        self._laptop:    dict  = {}
        self._phone_ts:  float = 0.0
        self._laptop_ts: float = 0.0

    # ── Writers ───────────────────────────────────────────────────────────────

    def update_phone(self, data: dict) -> None:
        self._phone.update({k: v for k, v in data.items() if v not in (None, "", [], {})})
        self._phone_ts = time.time()

    def update_laptop(self, data: dict) -> None:
        self._laptop.update({k: v for k, v in data.items() if v not in (None, "", [], {})})
        self._laptop_ts = time.time()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _age(self, ts: float) -> str:
        if ts == 0:
            return "?"
        secs = int(time.time() - ts)
        return f"{secs}s" if secs < 60 else f"{secs // 60}m"

    def has_data(self) -> bool:
        return bool(self._phone or self._laptop)

    # ── Context builder ───────────────────────────────────────────────────────

    def build_context_block(self) -> str:
        """Return a compact, LLM-readable device status block."""
        if not self.has_data():
            return ""

        lines = [f"[DEVICE CONTEXT — {_kst_now()} KST]"]

        if self._phone:
            p   = self._phone
            bat = f"{p.get('battery', '?')}%{'⚡' if p.get('charging') else ''}"
            row = [f"Battery {bat}"]
            if p.get('location_zone'): row.append(f"Zone: {p['location_zone']}")
            if p.get('wifi'):          row.append(f"WiFi: {p['wifi']}")
            if p.get('active_app'):    row.append(f"App: {p['active_app']}")
            lines.append(f"📱 Phone ({self._age(self._phone_ts)}): {' | '.join(row)}")

            notifs = p.get('notifications', [])
            if notifs:
                snippets = []
                for n in notifs[:4]:
                    sender = n.get('sender') or n.get('app') or '?'
                    text   = n.get('text', '')[:40]
                    snippets.append(f"{sender}: \"{text}\"")
                lines.append(f"📬 알림: {' • '.join(snippets)}")

        if self._laptop:
            l   = self._laptop
            bat = f"{l.get('battery', '?')}%{'⚡' if l.get('charging') else ''}"
            row = [f"CPU {l.get('cpu', '?')}%", f"RAM {l.get('ram', '?')}%", f"Battery {bat}"]
            if l.get('disk'): row.append(f"Disk {l['disk']}%")
            lines.append(f"💻 Laptop ({self._age(self._laptop_ts)}): {' | '.join(row)}")
            if l.get('active_window'):
                lines.append(f"🖥  Active: {l['active_window']}")

        return "\n".join(lines)

    def augment_message(self, user_message: str) -> str:
        """Prepend device context to a user message for LLM injection."""
        ctx = self.build_context_block()
        if not ctx:
            return user_message
        return f"{ctx}\n\n{user_message}"

    # ── Snapshot (for API) ────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "phone":          self._phone,
            "phone_updated":  self._age(self._phone_ts) if self._phone_ts else None,
            "laptop":         self._laptop,
            "laptop_updated": self._age(self._laptop_ts) if self._laptop_ts else None,
            "context_preview": self.build_context_block() or "(no data yet)",
        }


# Singleton
device_ctx = DeviceContext()
