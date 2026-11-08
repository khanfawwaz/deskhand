"""Controller-app API: pairing, per-device tokens, task control, live events, and the installable web app.

The Pilot app (any phone/laptop/tablet) talks ONLY to this API. It never reaches a target device directly: it
sends a goal to the server, the server runs the task, and the server's agent on the target device acts.

Workspaces (several people on one server, fully separate)
 - Every controller, laptop, task and learned experience belongs to ONE workspace. A controller token only ever sees
   and commands its own workspace; asking for anything in another one answers 404, exactly like "does not exist".
 - The server owner makes an INVITE code. Redeeming it creates a new workspace with its first controller. From then on
   that person adds their own controllers and laptops from inside the app (codes carry the workspace with them).

Security model
 - A device is added with a one-time pairing code made by the admin (CLI/dashboard) or by an existing controller of the
   same workspace. The code is single-use, expires after 10 minutes and wrong guesses are rate-limited per IP.
 - Pairing returns a long random token, shown once. Only its hash is stored; it can be revoked at any time.
 - Controller tokens can run/cancel tasks, answer approvals and press emergency stop. They cannot enroll devices,
   mint codes or see other clients. Those stay behind the admin key.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .states import TERMINAL

mimetypes.add_type("application/manifest+json", ".webmanifest")
WEB_DIR = Path(__file__).parent / "web"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"          # no 0/O/1/I/L: easy to read out and type
TERMINAL_VALUES = {s.value for s in TERMINAL}
NOTE_MAX = 160

APP_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
}


class Pairing:
    """One-time codes. A code can carry a small payload (which workspace it belongs to, or that it is an invite)."""

    def __init__(self, ttl: float = 600, max_failures: int = 8, window: float = 600):
        self.ttl, self.max_failures, self.window = ttl, max_failures, window
        self.codes: dict[str, float] = {}
        self.payloads: dict[str, dict] = {}
        self.failures: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def normalize(raw: str) -> str:
        return "".join(ch for ch in (raw or "").upper() if ch.isalnum())

    def create(self, payload: dict | None = None, ttl: float | None = None) -> str:
        now = time.time()
        self.codes = {c: e for c, e in self.codes.items() if e > now}          # forget expired codes
        self.payloads = {c: v for c, v in self.payloads.items() if c in self.codes}
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        self.codes[code] = now + (self.ttl if ttl is None else ttl)
        if payload:
            self.payloads[code] = dict(payload)
        return f"{code[:4]}-{code[4:]}"

    def take(self, raw: str, ip: str) -> dict | None:
        """Redeem a code: returns its payload ({} if it had none) or None if wrong/expired/used. Single use."""
        now = time.time()
        q = self.failures[ip]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.max_failures:
            raise HTTPException(429, "too many wrong codes from this address; wait a few minutes")
        code = self.normalize(raw)
        hit = None
        for known, expires in list(self.codes.items()):       # compare against every code: no timing leak
            if hmac.compare_digest(known, code) and expires > now:
                hit = known
        if hit is None:
            q.append(now)
            return None
        del self.codes[hit]                                    # single use
        return self.payloads.pop(hit, {})

    def redeem(self, raw: str, ip: str) -> bool:
        return self.take(raw, ip) is not None


class PairIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    name: str = Field("My device", min_length=1, max_length=60)
    workspace: str | None = Field(None, max_length=60)      # only used when redeeming an invite: what to call it


class InviteIn(BaseModel):
    max_tasks_per_day: int = Field(100, ge=1, le=100000)
    note: str = Field("", max_length=60)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    max_tasks_per_day: int | None = Field(None, ge=0, le=100000)
    disabled: bool | None = None


class TaskIn(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    device_id: str | None = None
    max_actions: int = Field(30, ge=1, le=60)
    timeout_s: float = Field(420, gt=0, le=900)


class ApproveIn(BaseModel):
    approved: bool


def _note(a: dict) -> str:
    return str(a.get("error") or a.get("verification") or a.get("decision") or "")[:NOTE_MAX]


def task_view(store, orch, t: dict, steps: bool = True) -> dict:
    """What a controller app may see about a task. Typed text and file contents are already redacted in the store."""
    p = orch.pending_approval(t["id"])
    v: dict[str, Any] = {
        "id": t["id"], "goal": t["goal"], "device_id": t["device_id"], "status": t["status"],
        "created_at": t["created_at"], "updated_at": t["updated_at"], "result": t.get("result"),
        "error": t.get("error"), "terminal": t["status"] in TERMINAL_VALUES,
        "pending_approval": None if not p else {
            "tool": p.get("tool"), "reason": p.get("reason"), "question": p.get("question"),
            "params": p.get("params")},
    }
    if steps:
        v["steps"] = [{"seq": a["seq"], "tool": a["tool"], "status": a["status"], "note": _note(a)}
                      for a in store.list_actions(t["id"])]
    return v


def install_client_api(app: FastAPI, store, hub, orch, admin_auth: Callable) -> Pairing:
    pairing = Pairing()
    app.state.pairing = pairing
    invites = Pairing(ttl=7 * 24 * 3600)             # an invite stays valid for a week, once
    app.state.invites = invites

    def client(authorization: str = Header(default="")) -> dict:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        c = store.authenticate_client(token) if token else None
        if not c:
            raise HTTPException(401, "this device is not paired (or its access was revoked)")
        c["workspace"] = c.get("workspace") or "main"
        w = store.get_workspace(c["workspace"])
        if w is None or w.get("disabled"):
            raise HTTPException(403, "this workspace is switched off; ask the person who runs the server")
        c["_workspace"] = w
        return c

    def devices_view(ws: str) -> list[dict]:
        online = set(hub.online())
        return [{"device_id": d["device_id"], "name": d.get("name") or d["device_id"],
                 "online": d["device_id"] in online, "last_seen": d.get("last_seen")}
                for d in store.list_devices(workspace=ws) if not d.get("revoked")]

    def own_task(tid: str, c: dict) -> dict:
        t = store.get_task(tid)
        if not t or (t.get("workspace") or "main") != c["workspace"]:
            raise HTTPException(404, "unknown task")          # same answer for 'not yours' and 'not there'
        return t

    # ---------------------------------------------------------------- admin (server owner): codes, invites, workspaces
    @app.post("/pairing", dependencies=[Depends(admin_auth)])
    def make_code(workspace: str = "main"):
        if store.get_workspace(workspace) is None:
            raise HTTPException(404, "unknown workspace")
        return {"code": pairing.create({"workspace": workspace}), "expires_in": int(pairing.ttl),
                "how": "open /app on the new device, enter this code"}

    @app.post("/invites", dependencies=[Depends(admin_auth)])
    def make_invite(body: InviteIn | None = None):
        body = body or InviteIn()
        code = invites.create({"invite": True, "max_tasks_per_day": body.max_tasks_per_day, "note": body.note})
        return {"code": code, "expires_in": int(invites.ttl),
                "how": "send this to your friend: they open the app and enter it. It creates their own private workspace."}

    @app.get("/workspaces", dependencies=[Depends(admin_auth)])
    def workspaces():
        return [w | {"controllers": len([x for x in store.list_clients(w["id"]) if not x.get("revoked")]),
                     "laptops": len([x for x in store.list_devices(w["id"]) if not x.get("revoked")]),
                     "tasks_24h": store.count_tasks_since(w["id"], time.time() - 86400)}
                for w in store.list_workspaces()]

    @app.patch("/workspaces/{wid}", dependencies=[Depends(admin_auth)])
    def update_workspace(wid: str, body: WorkspaceUpdate):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if not store.update_workspace(wid, **fields):
            raise HTTPException(404, "unknown workspace")
        return store.get_workspace(wid)

    @app.get("/clients", dependencies=[Depends(admin_auth)])
    def clients():
        return store.list_clients()

    @app.delete("/clients/{client_id}", dependencies=[Depends(admin_auth)])
    def unpair(client_id: str):
        if not store.revoke_client(client_id):
            raise HTTPException(404, "unknown client")
        return {"revoked": client_id}

    # ---------------------------------------------------------------- controller app
    @app.post("/v1/pair")
    def pair(body: PairIn, request: Request):
        ip = request.client.host if request.client else "?"
        payload = pairing.take(body.code, ip)
        invite = False
        if payload is None:                              # not a controller code: maybe an invite
            payload = invites.take(body.code, ip)
            invite = payload is not None
        if payload is None:
            raise HTTPException(403, "that code is wrong or has expired; ask for a new one")
        if invite:
            wname = (body.workspace or f"{body.name}'s workspace").strip()[:60]
            ws = store.create_workspace(wname, int(payload.get("max_tasks_per_day", 100)))
        else:
            ws = payload.get("workspace") or "main"
            if store.get_workspace(ws) is None:
                raise HTTPException(403, "that code is wrong or has expired; ask for a new one")
        cid, token = store.create_client(body.name, workspace=ws)
        return {"client_id": cid, "token": token, "name": body.name, "workspace": ws, "new_workspace": invite,
                "note": "this token is shown once and stored only on this device"}

    @app.get("/v1/me")
    def me(c: dict = Depends(client)):
        w = c["_workspace"]
        return {"client_id": c["client_id"], "name": c["name"], "workspace": {"id": w["id"], "name": w["name"]},
                "devices": devices_view(c["workspace"])}

    @app.get("/v1/workspace")
    def workspace_info(c: dict = Depends(client)):
        w = c["_workspace"]
        return {"id": w["id"], "name": w["name"], "max_tasks_per_day": w["max_tasks_per_day"],
                "tasks_24h": store.count_tasks_since(w["id"], time.time() - 86400)}

    @app.post("/v1/pair-code")
    def pair_code(c: dict = Depends(client)):
        """Any controller of a workspace can add another controller to the SAME workspace."""
        return {"code": pairing.create({"workspace": c["workspace"]}), "expires_in": int(pairing.ttl),
                "how": "open the app on the new device and enter this code"}

    @app.get("/v1/members")
    def members(c: dict = Depends(client)):
        return [{"client_id": m["client_id"], "name": m["name"], "last_seen": m.get("last_seen"),
                 "you": m["client_id"] == c["client_id"]}
                for m in store.list_clients(c["workspace"]) if not m.get("revoked")]

    @app.delete("/v1/members/{client_id}")
    def remove_member(client_id: str, c: dict = Depends(client)):
        if not store.revoke_client(client_id, workspace=c["workspace"]):
            raise HTTPException(404, "unknown controller")
        return {"revoked": client_id}

    @app.delete("/v1/devices/{device_id}")
    def remove_device(device_id: str, c: dict = Depends(client)):
        d = store.get_device(device_id)
        if not d or (d.get("workspace") or "main") != c["workspace"]:
            raise HTTPException(404, "unknown device")
        store.revoke_device(device_id)
        return {"revoked": device_id}

    @app.post("/v1/unpair")
    def unpair_self(c: dict = Depends(client)):
        store.revoke_client(c["client_id"])
        return {"unpaired": True}

    @app.get("/v1/devices")
    def devices(c: dict = Depends(client)):
        return devices_view(c["workspace"])

    @app.post("/v1/tasks", status_code=202)
    async def submit(body: TaskIn, c: dict = Depends(client)):
        ws, w = c["workspace"], c["_workspace"]
        mine = devices_view(ws)
        dev = body.device_id
        if not dev:
            online = [d for d in mine if d["online"]]
            pick = online if len(online) == 1 else (mine if len(mine) == 1 and not online else [])
            if len(pick) != 1:
                raise HTTPException(422, "choose which device to run this on (none or several are available)")
            dev = pick[0]["device_id"]
        if dev not in {d["device_id"] for d in mine}:
            raise HTTPException(404, f"device '{dev}' is not enrolled")        # another workspace's laptop looks absent
        if store.count_tasks_since(ws, time.time() - 86400) >= int(w.get("max_tasks_per_day", 100)):
            raise HTTPException(429, "this workspace reached its daily task limit; try again tomorrow")
        try:
            tid = orch.submit(dev, body.goal.strip(), body.max_actions, body.timeout_s,
                              {"client_id": c["client_id"], "via": "app"})
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"task_id": tid, "device_id": dev}

    @app.get("/v1/tasks")
    def tasks(limit: int = 20, c: dict = Depends(client)):
        return [task_view(store, orch, t, steps=False)
                for t in store.list_tasks(max(1, min(limit, 100)), workspace=c["workspace"])]

    @app.get("/v1/tasks/{tid}")
    def task(tid: str, c: dict = Depends(client)):
        return task_view(store, orch, own_task(tid, c))

    @app.post("/v1/tasks/{tid}/cancel")
    async def cancel(tid: str, c: dict = Depends(client)):
        own_task(tid, c)
        return {"cancelled": await orch.cancel(tid)}

    @app.post("/v1/tasks/{tid}/approve")
    def approve(tid: str, body: ApproveIn, c: dict = Depends(client)):
        own_task(tid, c)
        if not orch.approve(tid, body.approved):
            raise HTTPException(409, "nothing is waiting for approval on this task")
        return {"approved": body.approved}

    @app.post("/v1/estop")
    async def estop(c: dict = Depends(client)):
        await orch.emergency_stop(workspace=c["workspace"])          # only YOUR tasks and laptops
        return {"stopped": True}

    @app.get("/v1/events")
    async def events(request: Request, once: bool = False, c: dict = Depends(client)):
        """Server-sent events: a `devices` event, then a `task` event whenever a task changes. `once=1` returns the
        current snapshot and closes (also a polling fallback for clients that cannot stream)."""
        ws = c["workspace"]

        async def gen():
            seen: dict[str, tuple] = {}
            last_devices = None
            beat = time.monotonic()
            while True:
                dv = devices_view(ws)
                key = [(d["device_id"], d["online"]) for d in dv]
                if key != last_devices:
                    last_devices = key
                    yield f"event: devices\ndata: {json.dumps(dv)}\n\n"
                for t in reversed(store.list_tasks(12, workspace=ws)):
                    done = t["status"] in TERMINAL_VALUES
                    n = 0 if done and t["id"] in seen else len(store.list_actions(t["id"]))
                    fp = (t["status"], t["updated_at"], n if not done else seen.get(t["id"], (0, 0, 0))[2],
                          bool(orch.pending_approval(t["id"])))
                    if seen.get(t["id"]) != fp:
                        seen[t["id"]] = fp
                        yield f"event: task\ndata: {json.dumps(task_view(store, orch, t))}\n\n"
                if once or await request.is_disconnected():
                    break
                if time.monotonic() - beat > 15:
                    beat = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(0.7)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if path.startswith("/app"):
            for k, v in APP_HEADERS.items():
                resp.headers.setdefault(k, v)
            if path.endswith(("sw.js", "index.html", "/app/", "manifest.webmanifest")):
                resp.headers["Cache-Control"] = "no-cache"
        elif path.startswith(("/v1", "/pairing", "/clients", "/agent", "/i/", "/invites", "/workspaces")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="app")
    from .distribution import install_distribution
    install_distribution(app, store, client)              # one-line install + auto-update for laptops
    from .login import install_login
    install_login(app, store, Pairing)                     # self-service sign-in by email
    return pairing
