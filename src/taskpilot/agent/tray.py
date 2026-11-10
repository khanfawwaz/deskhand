"""Shared helpers for the Deskhand app's window and notification icon: reading the helper's status file, the address of the
web app, and the logo with a status dot. Nothing here talks to the server."""
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


def read_activity(home: Path, now: float | None = None) -> str:
    """'working', 'waiting' (for the person to pause) or '' when Deskhand is doing nothing."""
    now = time.time() if now is None else now
    try:
        d = json.loads((home / "activity.json").read_text(encoding="utf-8-sig"))
        if now - float(d.get("at", 0)) < 300 and d.get("state") in ("working", "waiting"):
            return str(d["state"])
    except (OSError, ValueError):
        pass
    return ""


def app_url(home: Path) -> str:
    try:
        cfg = json.loads((home / "agent.json").read_text(encoding="utf-8-sig"))
        base = cfg["server_url"].replace("wss://", "https://", 1).replace("ws://", "http://", 1).rsplit("/agent/ws", 1)[0]
        return base + "/app/"
    except (OSError, ValueError, KeyError):
        return ""


def badge(state: str, size: int = 64):
    """The logo with a small status dot: green online, amber starting, grey stopped."""
    from PIL import Image, ImageDraw
    from ..branding import draw_icon
    img = draw_icon(size)
    colour = {"online": (62, 207, 142), "connecting": (245, 185, 66), "working": (110, 217, 135),
              "waiting": (245, 185, 66)}.get(state, (140, 140, 150))
    d = ImageDraw.Draw(img)
    r = size * 0.16
    cx, cy = size - r - 1, size - r - 1
    d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=(7, 7, 29, 255))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour + (255,))
    return img
