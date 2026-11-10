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
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .datarights import CONFIRM_PHRASE
from . import chats
from .prefs import parse_preference
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
    continue_from: str | None = Field(None, max_length=40)   # the task this message continues (same chat); unknown ids are ignored


class ApproveIn(BaseModel):
    approved: bool


class AnswerIn(BaseModel):
    choice: str | None = Field(None, max_length=60)      # one of the offered option ids
    text: str | None = Field(None, max_length=1000)      # or free text (for questions)


class DeleteIn(BaseModel):
    confirm: str = Field("", max_length=60)             # must be the phrase "delete my data"


class AgentTaskIn(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)


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
            "params": p.get("params"), "kind": p.get("kind", "confirm"), "options": p.get("options") or [],
            "allow_text": bool(p.get("allow_text"))},
        "continue_from": chats.continue_of(t), "thread": chats.thread_root(store, t),    # chats are threads of tasks
    }
    if steps:
        v["steps"] = [{"seq": a["seq"], "tool": a["tool"], "status": a["status"], "note": _note(a)}
                      for a in store.list_actions(t["id"])]
    return v


def _budget_spent(meter) -> bool:
    from .usage import BudgetExceeded
    try:
        meter.check("")
        return False
    except BudgetExceeded:
        return True


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
        ext = getattr(app.state, "ext_status", {})          # told by each helper: is the Chrome extension connected?
        return [{"device_id": d["device_id"], "name": d.get("name") or d["device_id"],
                 "online": d["device_id"] in online, "last_seen": d.get("last_seen"),
                 "extension": bool(ext.get(d["device_id"], False)) and d["device_id"] in online}
                for d in store.list_devices(workspace=ws) if not d.get("revoked")]

    def note_if_preference(ws: str, dev: str, goal: str, ctx: dict) -> str | None:
        """"Remember this" / "set it as a preference" is NOT a task: save it, confirm it, and run nothing.
        Returns the finished (no-work) task id, or None when the message is an ordinary task."""
        pref = parse_preference(goal)
        if pref is None:
            return None
        if not pref["text"]:
            reply = ("I did not save anything: tell me what to remember, for example "
                     "“When I say play calming music, play lofi music, save it as a preference.”")
        else:
            store.add_preference(ws, pref["text"])
            reply = ("Saved as a preference: " + pref["text"] + "\nI have not done anything now. I will follow it "
                     "the next time it applies. You can see or delete your preferences in Settings.")
        return orch.submit_note(dev, goal.strip(), reply, ctx)

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
        from .login import extension_url
        w = c["_workspace"]
        return {"client_id": c["client_id"], "name": c["name"], "workspace": {"id": w["id"], "name": w["name"]},
                "devices": devices_view(c["workspace"]), "extension_url": extension_url()}

    @app.get("/v1/workspace")
    def workspace_info(c: dict = Depends(client)):
        w = c["_workspace"]
        return {"id": w["id"], "name": w["name"], "max_tasks_per_day": w["max_tasks_per_day"],
                "tasks_24h": store.count_tasks_since(w["id"], time.time() - 86400)}

    @app.get("/v1/usage")
    def my_usage(c: dict = Depends(client)):
        """What this workspace has used today, and how busy the shared model queue is."""
        w = c["_workspace"]
        meter, gate = getattr(app.state, "meter", None), getattr(app.state, "gate", None)
        mine = meter.today(w["id"]) if meter else {"calls": 0, "tokens": 0, "token_limit": 0}
        return {"tasks_24h": store.count_tasks_since(w["id"], time.time() - 86400), "max_tasks_per_day": w["max_tasks_per_day"],
                "model_calls_today": mine["calls"], "tokens_today": mine["tokens"], "token_limit": mine["token_limit"],
                "queue": ({"waiting": gate.waiting, "running": gate.in_flight} if gate else {"waiting": 0, "running": 0}),
                "paused_for_budget": bool(meter and _budget_spent(meter))}

    @app.get("/usage", dependencies=[Depends(admin_auth)])
    def all_usage(days: int = 7):
        """Server owner: today's totals and the budget, the model queue, and the busiest workspaces."""
        meter, gate = getattr(app.state, "meter", None), getattr(app.state, "gate", None)
        if meter is None:
            raise HTTPException(404, "usage tracking is not running (demo mode)")
        since = time.time() - max(1, min(days, 90)) * 86400
        return {"today": meter.today(), "queue": gate.status() if gate else None,
                f"last_{days}_days": store.usage_since(since), "workspaces": {w["id"]: w["name"] for w in store.list_workspaces()}}

    # ---------------------------------------------------------------- the person's own data: take a copy, or erase it
    data_calls: dict[str, deque] = defaultdict(deque)

    def data_limit(key: str, limit: int, window: float = 3600.0) -> None:
        """A few per hour per controller: enough for real use, not enough to hammer the database."""
        now, q = time.monotonic(), data_calls[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "too many requests: try again in a little while")
        q.append(now)

    @app.get("/v1/export")
    def export_my_data(c: dict = Depends(client)):
        """Everything stored about this workspace as one JSON file (never sign-in tokens, never another workspace)."""
        data_limit("export:" + c["client_id"], 6)
        doc = store.export_workspace(c["workspace"])
        name = "deskhand-data-" + time.strftime("%Y-%m-%d") + ".json"
        return Response(json.dumps(doc, default=str, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.post("/v1/delete-account")
    async def delete_my_data(body: DeleteIn, c: dict = Depends(client)):
        """Stop everything running, then erase this workspace: tasks and their steps, computers, signed-in devices,
        learned notes, usage counts and the workspace itself. It cannot be undone."""
        data_limit("delete:" + c["client_id"], 5)
        if " ".join((body.confirm or "").lower().split()) != CONFIRM_PHRASE:
            raise HTTPException(422, f'type "{CONFIRM_PHRASE}" to confirm')
        return {"deleted": True, "removed": await erase_workspace(c["workspace"])}

    async def erase_workspace(ws: str) -> dict:
        await orch.emergency_stop(workspace=ws)                     # nothing may still be running on these computers
        for _ in range(30):                                         # give the stopped tasks a moment to wind down
            if not store.active_task_ids(ws):
                break
            await asyncio.sleep(0.1)
        return store.delete_workspace_data(ws)

    @app.delete("/workspaces/{wid}", dependencies=[Depends(admin_auth)])
    async def erase_workspace_admin(wid: str):
        """Server owner: erase one workspace for someone who cannot sign in to do it themselves (a data-deletion request)."""
        if store.get_workspace(wid) is None:
            raise HTTPException(404, "unknown workspace")
        return {"deleted": True, "removed": await erase_workspace(wid)}

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
        ctx = {"client_id": c["client_id"], "via": "app"}
        if body.continue_from:                                              # a follow-up in an open chat
            prev = store.get_task(body.continue_from)
            if prev and (prev.get("workspace") or "main") == ws:            # someone else's task id is silently ignored
                ctx["continue_from"] = body.continue_from
        if (nid := note_if_preference(ws, dev, body.goal, ctx)):
            return {"task_id": nid, "device_id": dev}                      # a saved preference: nothing was run
        if store.count_tasks_since(ws, time.time() - 86400) >= int(w.get("max_tasks_per_day", 100)):
            raise HTTPException(429, "this workspace reached its daily task limit; try again tomorrow")
        try:
            tid = orch.submit(dev, body.goal.strip(), body.max_actions, body.timeout_s, ctx)
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

    @app.post("/v1/tasks/{tid}/answer")
    def answer(tid: str, body: AnswerIn, c: dict = Depends(client)):
        own_task(tid, c)
        if not orch.answer(tid, body.choice, body.text):
            raise HTTPException(409, "nothing is waiting for that answer on this task")
        return {"answered": True}

    # ---------------------------------------------------------------- the computer's own shortcut (Alt+F8 and say a command)
    # The helper on a laptop may start a task for ITSELF and follow it. It proves who it is with its device token; it can only
    # ever touch tasks of its own device, and every task counts against its workspace's daily limit like any other.
    submits: dict[str, deque] = defaultdict(deque)

    def device_auth(request: Request) -> dict:
        dev = request.headers.get("x-device-id", "")
        tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not dev or not tok or not store.authenticate_device(dev, tok):
            raise HTTPException(401, "invalid device token")
        d = store.get_device(dev) or {}
        return {"device_id": dev, "workspace": d.get("workspace", "main")}

    def device_task(tid: str, d: dict) -> dict:
        t = store.get_task(tid)
        if not t or t["device_id"] != d["device_id"]:
            raise HTTPException(404, "unknown task")
        return t

    @app.post("/agent/tasks", status_code=202)
    async def agent_submit(body: AgentTaskIn, d: dict = Depends(device_auth)):
        now = time.time()
        q = submits[d["device_id"]]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= 12:
            raise HTTPException(429, "too many commands in a minute: wait a moment")
        w = store.get_workspace(d["workspace"]) or {}
        if w.get("disabled"):
            raise HTTPException(403, "this workspace is switched off")
        if store.count_tasks_since(d["workspace"], now - 86400) >= int(w.get("max_tasks_per_day", 100)):
            raise HTTPException(429, "this workspace reached its daily task limit; try again tomorrow")
        if (nid := note_if_preference(d["workspace"], d["device_id"], body.goal, {"via": "shortcut", "device": d["device_id"]})):
            q.append(now)
            return {"task_id": nid}                                          # a saved preference: nothing was run
        try:
            tid = orch.submit(d["device_id"], body.goal.strip(), 30, 420, {"via": "shortcut", "device": d["device_id"]})
        except ValueError as e:
            raise HTTPException(404, str(e))
        q.append(now)
        return {"task_id": tid}

    @app.get("/agent/tasks/{tid}")
    def agent_task(tid: str, d: dict = Depends(device_auth)):
        return task_view(store, orch, device_task(tid, d), steps=False)

    @app.post("/agent/tasks/{tid}/cancel")
    async def agent_cancel(tid: str, d: dict = Depends(device_auth)):
        device_task(tid, d)
        return {"cancelled": await orch.cancel(tid)}

    @app.post("/agent/tasks/{tid}/approve")
    def agent_approve(tid: str, body: ApproveIn, d: dict = Depends(device_auth)):
        device_task(tid, d)
        if not orch.approve(tid, body.approved):
            raise HTTPException(409, "nothing is waiting for approval on this task")
        return {"approved": body.approved}

    @app.post("/agent/tasks/{tid}/answer")
    def agent_answer(tid: str, body: AnswerIn, d: dict = Depends(device_auth)):
        device_task(tid, d)
        if not orch.answer(tid, body.choice, body.text):
            raise HTTPException(409, "nothing is waiting for that answer on this task")
        return {"answered": True}

    # ---------------------------------------------------------------- what Deskhand has learned (per workspace)
    @app.get("/v1/skills")
    def skills_list(c: dict = Depends(client)):
        return [{"id": s["id"], "title": s.get("title"), "kind": s.get("kind"), "runs": s.get("runs", {}),
                 "version": s.get("version", 1), "updated_at": s.get("updated_at")}
                for s in store.list_skills(c["workspace"])]

    @app.get("/v1/skills/{sid:path}")
    def skill_get(sid: str, c: dict = Depends(client)):
        from .skills import render
        s = store.get_skill(c["workspace"], sid)
        if s is None:
            raise HTTPException(404, "unknown skill")
        return {"id": sid, "title": s.get("title"), "kind": s.get("kind"), "runs": s.get("runs", {}),
                "version": s.get("version", 1), "updated_at": s.get("updated_at"), "text": render(s, 3000),
                "pitfalls": [p.get("text") for p in s.get("pitfalls", [])]}

    @app.delete("/v1/skills/{sid:path}")
    def skill_forget(sid: str, c: dict = Depends(client)):
        if not store.delete_skill(c["workspace"], sid):
            raise HTTPException(404, "unknown skill")
        return {"forgotten": sid}

    @app.get("/v1/preferences")
    def preferences(c: dict = Depends(client)):
        return store.list_preferences(c["workspace"])

    @app.delete("/v1/preferences/{pid}")
    def preference_forget(pid: str, c: dict = Depends(client)):
        if not store.delete_preference(c["workspace"], pid):
            raise HTTPException(404, "unknown preference")
        return {"forgotten": pid}

    def drop_chat(ws: str, ids: list[str]) -> int:
        n = store.delete_tasks(ids)
        orch.forget(ids)
        return n

    def running_in(ids: list[str]) -> bool:
        return any((store.get_task(i) or {}).get("status") not in TERMINAL_VALUES for i in ids if store.get_task(i))

    @app.delete("/v1/chats/{tid}")
    def delete_chat(tid: str, c: dict = Depends(client)):
        """Delete a whole chat: every task in the thread that contains `tid`, with its steps. Only your own."""
        ids = chats.thread_ids(store, c["workspace"], tid)
        if not ids:
            raise HTTPException(404, "unknown chat")                       # same answer for 'not yours' and 'not there'
        if running_in(ids):
            raise HTTPException(409, "This chat is still running. Press Stop first, then delete it.")
        return {"deleted": drop_chat(c["workspace"], ids), "ids": ids}

    @app.delete("/v1/chats")
    def delete_finished_chats(c: dict = Depends(client)):
        """Delete every chat that has nothing running. Chats with a running task are left alone."""
        ids = [i for g in chats.finished_chats(store, c["workspace"]) for i in g]
        return {"deleted": drop_chat(c["workspace"], ids), "chats": len(ids)}

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
                key = [(d["device_id"], d["online"], d["extension"]) for d in dv]
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
            resp.headers.setdefault("Cache-Control", "no-cache")   # always re-check (ETag): an update must show up at once
            #                                                        (setdefault: the new app's hashed files say "immutable" themselves)
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
