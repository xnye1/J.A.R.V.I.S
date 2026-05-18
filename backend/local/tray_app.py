"""
local/tray_app.py — JARVIS Windows System Tray

Run once at Windows startup (or via JARVIS_Start.vbs):
  pythonw backend/local/tray_app.py

Features:
  • Starts laptop_agent.py as a managed subprocess
  • Watchdog thread auto-restarts agent on crash (5-s heartbeat)
  • Tray icon: cyan = agent running, gray = stopped
  • Menu: HUD, Remote, Restart Agent, Auto-start toggle, Quit
  • Auto-start installs/removes JARVIS.bat from Windows Startup folder
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────────────────────

JARVIS_URL   = os.getenv("JARVIS_URL", "https://jarvis-yejun.duckdns.org")
HUD_URL      = JARVIS_URL + "/hud"
REMOTE_URL   = JARVIS_URL + "/remote"
SCRIPT_DIR   = Path(__file__).parent
AGENT_SCRIPT = SCRIPT_DIR / "laptop_agent.py"
PYTHON       = sys.executable


# ── Icon builder ──────────────────────────────────────────────────────────────

def _make_icon(online: bool) -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)

    bg   = (0, 8, 20, 255)
    fg   = (0, 212, 255, 255) if online else (80, 80, 80, 255)
    ring = max(2, size // 32)

    d.ellipse([0, 0, size - 1, size - 1], fill=bg)
    d.ellipse([ring, ring, size - ring - 1, size - ring - 1],
              outline=fg, width=ring)

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 36)
    except Exception:
        font = ImageFont.load_default()

    bbox = d.textbbox((0, 0), "J", font=font)
    x = (size - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (size - (bbox[3] - bbox[1])) // 2 - bbox[1] - 2
    d.text((x, y), "J", fill=fg, font=font)
    return img


# ── Startup folder helper ─────────────────────────────────────────────────────

def _startup_dir() -> Path:
    return Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"


def _bat_path() -> Path:
    return _startup_dir() / "JARVIS.bat"


# ── Tray application ──────────────────────────────────────────────────────────

class JarvisTray:
    def __init__(self) -> None:
        self._proc:    subprocess.Popen | None = None
        self._icon:    pystray.Icon | None     = None
        self._alive    = True

    # ── Agent lifecycle ───────────────────────────────────────────────────────

    def _start_agent(self) -> None:
        if self._is_alive():
            return
        try:
            self._proc = subprocess.Popen(
                [PYTHON, str(AGENT_SCRIPT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._set_icon(online=True)
            self._notify("JARVIS Agent started")
        except Exception as exc:
            self._notify(f"Agent start failed: {exc}")

    def _stop_agent(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._set_icon(online=False)

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Icon / title ──────────────────────────────────────────────────────────

    def _set_icon(self, online: bool) -> None:
        if self._icon:
            self._icon.icon  = _make_icon(online)
            self._icon.title = "JARVIS — ONLINE" if online else "JARVIS — OFFLINE"

    def _notify(self, msg: str) -> None:
        if self._icon:
            try:
                self._icon.notify(msg, "JARVIS")
            except Exception:
                pass

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog(self) -> None:
        while self._alive:
            if self._proc is not None and self._proc.poll() is not None:
                # Agent crashed — wait briefly then restart
                time.sleep(3)
                if self._alive:
                    self._notify("Agent crashed — restarting…")
                    self._start_agent()
            time.sleep(5)

    # ── Menu callbacks ────────────────────────────────────────────────────────

    def _open_hud(self, icon, item) -> None:
        webbrowser.open(HUD_URL)

    def _open_remote(self, icon, item) -> None:
        webbrowser.open(REMOTE_URL)

    def _restart(self, icon, item) -> None:
        self._stop_agent()
        time.sleep(1)
        self._start_agent()

    def _toggle_autostart(self, icon, item) -> None:
        bat = _bat_path()
        if bat.exists():
            bat.unlink()
            self._notify("자동 시작 비활성화")
        else:
            # Point to this file so the tray itself auto-launches
            tray_script = Path(__file__).resolve()
            # Use pythonw (no console window) if available
            pythonw = Path(PYTHON).parent / "pythonw.exe"
            runner  = str(pythonw) if pythonw.exists() else PYTHON
            bat.write_text(
                f'@echo off\nstart "" "{runner}" "{tray_script}"\n',
                encoding="utf-8",
            )
            self._notify("자동 시작 활성화 — 다음 로그인 시 자동 실행됩니다")
        icon.update_menu()

    def _is_autostart(self, item) -> bool:
        return _bat_path().exists()

    def _quit(self, icon, item) -> None:
        self._alive = False
        self._stop_agent()
        icon.stop()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self._start_agent()

        threading.Thread(target=self._watchdog, daemon=True).start()

        menu = pystray.Menu(
            pystray.MenuItem("HUD 열기",    self._open_hud,    default=True),
            pystray.MenuItem("Remote 열기", self._open_remote),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Agent 재시작", self._restart),
            pystray.MenuItem(
                "Windows 시작 시 자동 실행",
                self._toggle_autostart,
                checked=self._is_autostart,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._quit),
        )

        self._icon = pystray.Icon(
            name="JARVIS",
            icon=_make_icon(online=True),
            title="JARVIS — ONLINE",
            menu=menu,
        )
        self._icon.run()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    JarvisTray().run()
