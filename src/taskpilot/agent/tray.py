"""The Deskhand icon in the Windows notification area: shows whether this computer is online and lets you start, stop
or open Deskhand without touching a command line.

It never talks to the server itself. It only reads `status.json`, which the helper keeps up to date, and starts/stops
the helper's scheduled task. Needs pystray + Pillow.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

TASK = "DeskhandAgent"
FRESH_S = 35                      # the helper refreshes the file every ~10 s; older than this = not running
NO_WINDOW = 0x08000000


def status_path(home: Path) -> Path:
    return home / "status.json"


def read_status(home: Path, now: float | None = None) -> tuple[str, str]:
    """-> (state, human text). state is 'online', 'connecting' or 'stopped'."""
    now = time.time() if now is None else now
    try:
        d = json.loads(status_path(home).read_text(encoding="utf-8-sig"))
        age = now - float(d.get("at", 0))
    except (OSError, ValueError):
        return "stopped", "Deskhand is not running"
    if age > FRESH_S:
        return "stopped", "Deskhand is not running"
    if d.get("connected"):
        return "online", "Online: you can control this computer"
    return "connecting", "Starting: connecting to the server"


def app_url(home: Path) -> str:
    try:
        cfg = json.loads((home / "agent.json").read_text(encoding="utf-8-sig"))
        base = cfg["server_url"].replace("wss://", "https://", 1).replace("ws://", "http://", 1).rsplit("/agent/ws", 1)[0]
        return base + "/app/"
    except (OSError, ValueError, KeyError):
        return ""


def _schtasks(verb: str) -> None:
    subprocess.run(["schtasks", verb, "/tn", TASK], capture_output=True, creationflags=NO_WINDOW)


def _single_instance() -> bool:
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Deskhand.Tray.Singleton")
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def badge(state: str, size: int = 64):
    """The logo with a small status dot: green online, amber starting, grey stopped."""
    from PIL import Image, ImageDraw
    from ..branding import draw_icon
    img = draw_icon(size)
    colour = {"online": (62, 207, 142), "connecting": (245, 185, 66)}.get(state, (140, 140, 150))
    d = ImageDraw.Draw(img)
    r = size * 0.16
    cx, cy = size - r - 1, size - r - 1
    d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=(7, 7, 29, 255))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour + (255,))
    return img


def run(home: Path) -> None:
    import pystray
    if os.name == "nt" and not _single_instance():
        return
    state, text = read_status(home)

    def open_app(icon=None, item=None):
        url = app_url(home)
        if url:
            webbrowser.open(url)

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: read_status(home)[1], None, enabled=False),
        pystray.MenuItem("Open Deskhand", open_app, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start", lambda icon, item: _schtasks("/run")),
        pystray.MenuItem("Stop", lambda icon, item: _schtasks("/end")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit this icon", lambda icon, item: icon.stop()),
    )
    icon = pystray.Icon("Deskhand", badge(state), "Deskhand: " + text, menu)

    def watch():
        last = state
        while True:
            time.sleep(3)
            s, t = read_status(home)
            if s != last:
                icon.icon = badge(s)
                last = s
            icon.title = "Deskhand: " + t
            try:
                icon.update_menu()
            except Exception:
                pass
    icon.run(setup=lambda i: (setattr(i, "visible", True), threading.Thread(target=watch, daemon=True).start()))


def main() -> None:
    home = Path(os.environ.get("PILOT_HOME") or (sys.argv[1] if len(sys.argv) > 1 else Path.cwd()))
    run(home)


if __name__ == "__main__":
    main()
