"""Extension bridge: drive the user's OWN Chrome (with their logins) through the Deskhand browser extension.

Chrome will not open a debugging port on a normal profile, and a copied profile loses its logins. The extension runs inside
the real profile instead: it connects OUT to this local server (127.0.0.1 only) and runs DevTools commands with
chrome.debugger. Nothing leaves this computer; passwords and cookies are never read or sent anywhere.

Pairing: the first time, the extension asks to connect and the user must approve it in a Deskhand dialog on this computer
(so a web page or another program cannot attach itself). The approval gives that extension a secret token, which is stored
here (hashed) and in the extension; later connections must present it AND come from the same extension id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Callable

PORT = int(os.environ.get("DESKHAND_EXT_PORT", "9223"))


def store_file() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(os.environ.get("DESKHAND_PAIRED_FILE", str(Path(root) / "Deskhand" / "paired-extensions.json")))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def windows_approval(extension_id: str) -> bool:
    """Ask the person at the computer. A native Yes/No box, always on top."""
    if sys.platform != "win32":
        return False
    import ctypes
    text = ("A Chrome extension is asking to connect to Deskhand so it can carry out your web tasks in your browser.\n\n"
            f"Extension id: {extension_id}\n\nOnly choose Yes if you just pressed 'Connect to Deskhand' in Chrome.")
    # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2 (No) | MB_SETFOREGROUND | MB_TOPMOST
    return ctypes.windll.user32.MessageBoxW(0, text, "Deskhand: connect Chrome?", 0x4 | 0x20 | 0x100 | 0x10000 | 0x40000) == 6


class ExtBridge:
    """Local server the extension connects to. One request at a time, matched by id."""

    def __init__(self, port: int = PORT, approve: Callable[[str], bool] | None = None, paired_file: Path | None = None):
        self.port = port
        self.approve = approve or windows_approval
        self.paired_file = paired_file or store_file()
        self._ws = None
        self._lock = threading.Lock()
        self._n = 0
        self._server = None
        self._started = False

    # ---- who is paired
    def _paired(self) -> dict:
        try:
            return json.loads(self.paired_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, ext_id: str, token: str) -> None:
        d = self._paired()
        d[ext_id] = _digest(token)
        self.paired_file.parent.mkdir(parents=True, exist_ok=True)
        self.paired_file.write_text(json.dumps(d), encoding="utf-8")

    def unpair_all(self) -> None:
        try:
            self.paired_file.unlink()
        except OSError:
            pass

    # ---- server side
    def start(self) -> None:
        if self._started:
            return
        from websockets.sync.server import serve
        self._server = serve(self._on_connect, "127.0.0.1", self.port, max_size=32 * 1024 * 1024)
        threading.Thread(target=self._server.serve_forever, name="deskhand-ext-bridge", daemon=True).start()
        self._started = True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
        self._started = False

    @staticmethod
    def _extension_id(ws) -> str:
        origin = (ws.request.headers.get("Origin") or "") if getattr(ws, "request", None) else ""
        return origin.removeprefix("chrome-extension://").rstrip("/") if origin.startswith("chrome-extension://") else ""

    def _on_connect(self, ws) -> None:
        ext_id = self._extension_id(ws)
        path = getattr(ws.request, "path", "") or ""
        if not ext_id:                                   # a web page or another program: never allowed
            ws.close(1008, "not from a browser extension")
            return
        if "pair=1" in path:
            if not self.approve(ext_id):
                ws.send(json.dumps({"denied": "declined on the computer"}))
                ws.close(1000)
                return
            token = secrets.token_urlsafe(32)
            self._remember(ext_id, token)
            ws.send(json.dumps({"paired": token}))
        else:
            token = path.partition("token=")[2].partition("&")[0]
            known = self._paired().get(ext_id, "")
            if not token or not known or not hmac.compare_digest(known, _digest(token)):
                ws.send(json.dumps({"denied": "not paired"}))
                ws.close(1008, "not paired")
                return
            ws.send(json.dumps({"welcome": True}))
        self._ws = ws
        try:
            # replies are read by request(); this only keeps the connection open until it closes
            while self._ws is ws and ws.protocol.state.name == "OPEN":
                time.sleep(0.3)
        except Exception:
            pass
        finally:
            if self._ws is ws:
                self._ws = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def wait_connected(self, timeout: float = 4.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.connected:
                return True
            time.sleep(0.2)
        return self.connected

    def request(self, msg: dict, timeout: float = 25.0):
        ws = self._ws
        if ws is None:
            raise RuntimeError("the Deskhand browser extension is not connected")
        with self._lock:
            self._n += 1
            mid = self._n
            ws.send(json.dumps({**msg, "id": mid}))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    raw = ws.recv(timeout=max(0.1, end - time.monotonic()))
                except TimeoutError:
                    break
                try:
                    reply = json.loads(raw)
                except ValueError:
                    continue
                if reply.get("id") == mid:
                    if "error" in reply:
                        raise RuntimeError(str(reply["error"])[:200])
                    return reply.get("result")
        raise RuntimeError(f"no answer from the browser extension for {msg.get('cmd')}")


_BRIDGE: ExtBridge | None = None


def get_bridge() -> ExtBridge:
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = ExtBridge()
        try:
            _BRIDGE.start()
        except OSError:                                    # port in use: another Deskhand process owns it
            pass
    return _BRIDGE
