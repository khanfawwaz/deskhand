"""Hand-out of the agent: one-line install, join with a one-time code, and automatic updates.

The server is the single place devices get code from. When you push to GitHub and the server pulls, the package's
content hash changes, `/agent/version` reports it, and every installed agent downloads the new bundle and restarts
itself. Devices never talk to GitHub, so a private repository needs no secrets on them.

  /v1/add-device   (controller auth)  -> mints a one-time code + the one-line install command
  /i/{code}        (public)           -> the install script with the server address and that code filled in
  /agent/join      (code)             -> enrolls the laptop, returns its token (shown once, never stored by the server)
  /agent/version, /agent/bundle.zip   (device token) -> what the updater polls and downloads
"""
from __future__ import annotations

import hashlib
import io
import re
import secrets
import zipfile
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

PKG = Path(__file__).resolve().parent
TEMPLATE = PKG / "agent" / "install_template.ps1"

# What the Windows agent needs installed in its private virtual environment (not part of the bundle).
AGENT_REQUIREMENTS = ["pydantic>=2", "httpx", "websockets", "psutil", "mss", "pyautogui", "pillow", "uiautomation"]


class Bundle:
    """The agent's code as a zip, with a version that is the hash of the code itself (so it changes on every push)."""

    def __init__(self, root: Path = PKG):
        self.root = root
        self._data = b""
        self._version = ""

    def _files(self) -> list[Path]:
        skip = {"web", "__pycache__", "evals"}
        return sorted(p for p in self.root.rglob("*.py") if not skip & set(p.relative_to(self.root).parts))

    def build(self) -> tuple[str, bytes]:
        """Hash the files on every call (about 60 small files: milliseconds), so a change is never missed;
        only the zip itself is cached per version."""
        items = [(p.relative_to(self.root).as_posix(), p.read_bytes()) for p in self._files()]
        h = hashlib.sha256()
        for rel, data in items:
            h.update(rel.encode() + b"\0" + data)
        version = h.hexdigest()[:12]
        if version != self._version:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for rel, data in items:
                    z.writestr(f"taskpilot/{rel}", data)
                z.writestr("requirements.txt", "\n".join(AGENT_REQUIREMENTS) + "\n")
            self._data, self._version = buf.getvalue(), version
        return self._version, self._data


class JoinIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    name: str = Field("Laptop", min_length=1, max_length=60)


def _device_id(name: str, exists: Callable[[str], bool]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")[:40] or "laptop"
    while True:
        cand = f"{base}-{secrets.token_hex(2)}"
        if not exists(cand):
            return cand


def _origin(request: Request) -> str:
    """The address the caller used to reach us (works behind Tailscale serve / a reverse proxy)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def install_distribution(app: FastAPI, store, client_dep: Callable, bundle: Bundle | None = None):
    from .client_api import Pairing
    bundle = bundle or Bundle()
    codes = Pairing(ttl=900)                       # separate from controller pairing: a laptop code can never pair a controller
    app.state.agent_codes = codes

    def device(x_device_id: str = Header(default=""), x_device_token: str = Header(default="")) -> str:
        if not x_device_id or not x_device_token or not store.authenticate_device(x_device_id, x_device_token):
            raise HTTPException(401, "unknown or revoked device")
        return x_device_id

    @app.post("/v1/add-device")
    def add_device(request: Request, _c: dict = Depends(client_dep)):
        code = codes.create({"workspace": _c["workspace"]})
        origin = _origin(request)
        return {"code": code, "expires_in": int(codes.ttl),
                "command": f"irm {origin}/i/{code} | iex",
                "how": "run this once in PowerShell on the Windows laptop you want to control"}

    @app.get("/i/{code}", response_class=PlainTextResponse)
    def install_script(code: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9-]{4,20}", code):
            raise HTTPException(404)
        text = TEMPLATE.read_text(encoding="utf-8")
        return PlainTextResponse(text.replace("__SERVER__", _origin(request)).replace("__CODE__", code))

    @app.post("/agent/join")
    def join(body: JoinIn, request: Request):
        ip = request.client.host if request.client else "?"
        payload = codes.take(body.code, ip)
        if payload is None:
            raise HTTPException(403, "that code is wrong, already used, or expired: make a new one in the app")
        did = _device_id(body.name, store.device_exists)
        token = store.enroll_device(did, body.name, workspace=payload.get("workspace", "main"))
        origin = _origin(request)
        ws = origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/agent/ws"
        return {"device_id": did, "token": token, "ws_url": ws}

    @app.get("/agent/version")
    def version(_d: str = Depends(device)):
        v, data = bundle.build()
        return {"version": v, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "requirements": AGENT_REQUIREMENTS}

    @app.get("/agent/bundle.zip")
    def download(_d: str = Depends(device)):
        _, data = bundle.build()
        return Response(content=data, media_type="application/zip")
