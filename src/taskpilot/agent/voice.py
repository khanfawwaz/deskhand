"""Say a command: the voice prompt behind the shortcut (Alt+F8).

Three small pieces:
  * ServerLink    talks to the Deskhand server AS THIS COMPUTER (its device token): start a task for itself, follow it, answer it.
  * VoiceBridge   a tiny web server on 127.0.0.1 inside the helper. It serves the prompt page and forwards the page's requests
                  to the ServerLink. Every request must carry a secret that only the page the shortcut opened knows, and
                  must come from that page: another website cannot use it.
  * launch_overlay  what the shortcut calls: open the prompt in a small browser app window.

The prompt listens with the browser's own speech recognition, so no extra software or speech key is needed.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .voice_ui import CSS, HTML, JS

log = logging.getLogger("taskpilot.voice")
TITLE = "Deskhand voice"
ASSETS = {"/voice": ("text/html; charset=utf-8", HTML), "/voice/voice.css": ("text/css; charset=utf-8", CSS),
          "/voice/voice.js": ("application/javascript; charset=utf-8", JS)}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"


class LinkError(Exception):
    """A plain-language reason the server could not do it."""


class ServerLink:
    def __init__(self, base_url: str, device_id: str, token: str, client=None):
        self.base, self.device_id, self.token = base_url.rstrip("/"), device_id, token
        self._client = client

    @classmethod
    def from_config(cls, cfg: dict, token: str) -> "ServerLink":
        url = str(cfg.get("server_url", ""))
        base = url.replace("wss://", "https://", 1).replace("ws://", "http://", 1).rsplit("/agent/ws", 1)[0]
        return cls(base, str(cfg.get("device_id", "")), token)

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        import httpx
        headers = {"Authorization": f"Bearer {self.token}", "X-Device-Id": self.device_id}
        try:
            if self._client is not None:
                r = self._client.request(method, self.base + path, json=body, headers=headers)
            else:
                with httpx.Client(timeout=20) as c:
                    r = c.request(method, self.base + path, json=body, headers=headers)
        except Exception as e:                                         # noqa: BLE001
            raise LinkError("Could not reach the Deskhand server. Check the internet connection.") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            detail = data.get("detail")
            raise LinkError(detail if isinstance(detail, str) else f"The server said no ({r.status_code}).")
        return data

    def submit(self, goal: str) -> dict:
        return self._call("POST", "/agent/tasks", {"goal": goal})

    def status(self, tid: str) -> dict:
        return self._call("GET", f"/agent/tasks/{tid}")

    def cancel(self, tid: str) -> dict:
        return self._call("POST", f"/agent/tasks/{tid}/cancel")

    def approve(self, tid: str, approved: bool) -> dict:
        return self._call("POST", f"/agent/tasks/{tid}/approve", {"approved": bool(approved)})

    def answer(self, tid: str, choice: str | None, text: str | None) -> dict:
        return self._call("POST", f"/agent/tasks/{tid}/answer", {"choice": choice, "text": text})


class VoiceBridge:
    def __init__(self, link, home: Path | None = None, secret: str | None = None, port: int = 0):
        self.link, self.home = link, Path(home) if home else None
        self.secret = secret or secrets.token_urlsafe(24)
        self._want_port = port
        self.port = 0
        self._server: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> int:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):                      # quiet
                return

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj: dict) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json")

            def _host_ok(self) -> bool:                      # DNS rebinding: only our own address is accepted
                return self.headers.get("Host", "") in (f"127.0.0.1:{bridge.port}", f"localhost:{bridge.port}")

            def _auth(self) -> bool:
                origin = self.headers.get("Origin")
                if origin and origin not in (f"http://127.0.0.1:{bridge.port}", f"http://localhost:{bridge.port}"):
                    return False
                return hmac.compare_digest(self.headers.get("X-Deskhand-Local", ""), bridge.secret)

            def do_GET(self):                               # noqa: N802
                if not self._host_ok():
                    return self._json(403, {"error": "wrong host"})
                u = urlparse(self.path)
                if u.path in ASSETS:                         # the page itself holds no secret: it reads it from the address it was opened at
                    ctype, body = ASSETS[u.path]
                    return self._send(200, body.encode("utf-8"), ctype)
                if u.path == "/voice/status":
                    if not self._auth():
                        return self._json(401, {"error": "not allowed"})
                    tid = (parse_qs(u.query).get("tid") or [""])[0]
                    return self._forward(lambda: bridge.link.status(tid))
                self._json(404, {"error": "not found"})

            def do_POST(self):                              # noqa: N802
                if not self._host_ok() or not self._auth():
                    return self._json(401, {"error": "not allowed"})
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
                except (ValueError, OSError):
                    return self._json(400, {"error": "bad request"})
                p = urlparse(self.path).path
                if p == "/voice/submit":
                    goal = str(body.get("goal", "")).strip()
                    if not goal or len(goal) > 1000:
                        return self._json(422, {"error": "say or type what you want done"})
                    return self._forward(lambda: bridge.link.submit(goal))
                tid = str(body.get("tid", ""))
                if p == "/voice/cancel":
                    return self._forward(lambda: bridge.link.cancel(tid))
                if p == "/voice/approve":
                    return self._forward(lambda: bridge.link.approve(tid, bool(body.get("approved"))))
                if p == "/voice/answer":
                    return self._forward(lambda: bridge.link.answer(tid, body.get("choice"), body.get("text")))
                self._json(404, {"error": "not found"})

            def _forward(self, fn):
                try:
                    self._json(200, fn())
                except LinkError as e:
                    self._json(502, {"error": str(e)})
                except Exception:                           # noqa: BLE001
                    log.exception("voice request failed")
                    self._json(500, {"error": "something went wrong"})

        self._server = ThreadingHTTPServer(("127.0.0.1", self._want_port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name="deskhand-voice", daemon=True).start()
        self._publish()
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self.home:
            try:
                (self.home / "voice.json").unlink()
            except OSError:
                pass

    def _publish(self) -> None:
        """Tell the Deskhand app (which owns the shortcut) where to open the prompt."""
        if not self.home:
            return
        try:
            tmp = self.home / "voice.json.tmp"
            tmp.write_text(json.dumps({"port": self.port, "secret": self.secret, "pid": os.getpid(), "at": time.time()}), encoding="utf-8")
            os.replace(tmp, self.home / "voice.json")
        except OSError:
            log.warning("could not publish the voice prompt address")


def start_for_helper(cfg: dict, token: str) -> VoiceBridge | None:
    """Called by the helper once it is running: the shortcut needs this to have somewhere to send what you say."""
    home = os.environ.get("PILOT_HOME")
    if not cfg.get("server_url") or not cfg.get("device_id") or not token:
        return None
    bridge = VoiceBridge(ServerLink.from_config(cfg, token), Path(home) if home else None)
    bridge.start()
    return bridge


# ---------------------------------------------------------------- opening the prompt (runs in the Deskhand app, on the shortcut)
def find_browser() -> str | None:
    from .cdp import chrome_path
    if (c := chrome_path()):
        return c
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(p).is_file():
            return p
    return None


def read_published(home: Path, now: float | None = None) -> dict | None:
    try:
        d = json.loads((home / "voice.json").read_text(encoding="utf-8-sig"))
        port = int(d["port"])
    except (OSError, ValueError, KeyError):
        return None
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.6).close()      # the helper that wrote it is really still there
    except OSError:
        return None
    return d


def overlay_args(exe: str, port: int, secret: str, profile: Path, screen: tuple[int, int] = (1920, 1080), scale: float = 1.0) -> list[str]:
    w, h = 560, 340
    x = max(0, int((screen[0] / scale - w) / 2))
    y = max(0, int(screen[1] / scale - h - 90))                                  # low and central, above the taskbar
    return [exe, f"--app=http://127.0.0.1:{port}/voice#{secret}", f"--window-size={w},{h}", f"--window-position={x},{y}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--use-fake-ui-for-media-stream",        # this private profile only ever opens the local prompt: the microphone is allowed for it
            "--disable-features=Translate,msEdgeTranslate"]


def launch_overlay(home: Path) -> tuple[bool, str]:
    """-> (opened, plain message when it could not)."""
    pub = read_published(home)
    if pub is None:
        return False, "Deskhand is not connected yet. Wait a moment and try again."
    exe = find_browser()
    if not exe:
        return False, "Chrome or Edge is needed for the voice prompt."
    import ctypes
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    if hwnd:                                                                     # already open: bring it forward (it listens again)
        u.ShowWindow(hwnd, 9)
        u.SetForegroundWindow(hwnd)
        return True, ""
    try:
        scale = u.GetDpiForSystem() / 96.0
    except Exception:
        scale = 1.0
    screen = (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
    profile = home / "voice-profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(overlay_args(exe, int(pub["port"]), str(pub["secret"]), profile, screen, scale),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return True, ""
