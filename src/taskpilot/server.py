"""FastAPI task API + authenticated agent WebSocket."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import statistics
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from typing import Any

from pydantic import BaseModel, Field

from .client_api import install_client_api
from .devices import DeviceHub, WebSocketConnection
from .model import ModelRuntime
from .orchestrator import Config, Orchestrator
from .policy import Policy
from .recipes import describe, parametrize, sync_recipe_tools, validate_recipe
from .tools import get_registry
from .store import Store, open_store

DASHBOARD = """<!doctype html><meta charset=utf-8><title>TaskPilot</title>
<body style="font:14px system-ui;max-width:900px;margin:2rem auto"><h2>TaskPilot</h2>
<input id=k placeholder="API key" type=password> <button onclick=load()>Load</button>
<h3>Devices</h3><pre id=d></pre><h3>Tasks</h3><pre id=t></pre>
<script>async function load(){const h={'X-API-Key':k.value};
d.textContent=JSON.stringify(await (await fetch('/devices',{headers:h})).json(),null,1);
t.textContent=JSON.stringify(await (await fetch('/tasks',{headers:h})).json(),null,1)}</script>"""


class TaskIn(BaseModel):
    device_id: str
    goal: str = Field(min_length=1, max_length=4000)
    max_actions: int = Field(25, ge=1, le=200)
    timeout_s: float = Field(300, gt=0, le=3600)


class EnrollIn(BaseModel):
    device_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    name: str = ""
    workspace: str = "main"


class ApproveIn(BaseModel):
    approved: bool = True


class RecipeApproveIn(BaseModel):
    new_name: str | None = None                     # rename a draft_xxx into a real tool name
    description: str | None = None
    params: dict[str, str] = Field(default_factory=dict)      # {param: literal seen in the recorded run}
    verify: list[dict] = Field(default_factory=list)          # [{"step": {ui.read ...}, "contains": "12"}]


class RecipeRunIn(BaseModel):
    device_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = Field(300, gt=0, le=3600)


def create_app(store: Store, hub: DeviceHub, orch: Orchestrator, admin_key: str) -> FastAPI:
    app = FastAPI(title="TaskPilot", version="0.1.0")
    sockets: dict[str, WebSocket] = {}
    sync_recipe_tools(get_registry(), store)        # approved recipes become tools the model can call

    def auth(x_api_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_api_key, admin_key):
            raise HTTPException(401, "invalid API key")

    @app.get("/admin", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD

    # ------------------------------------------------------------ devices
    @app.post("/devices", dependencies=[Depends(auth)])
    def enroll(body: EnrollIn):
        if store.device_exists(body.device_id):
            raise HTTPException(409, "device already enrolled")
        return {"device_id": body.device_id, "token": store.enroll_device(body.device_id, body.name, workspace=body.workspace),
                "note": "store this token now; it cannot be shown again"}

    @app.get("/devices", dependencies=[Depends(auth)])
    def devices():
        online = set(hub.online())
        return [d | {"online": d["device_id"] in online} for d in store.list_devices()]

    @app.delete("/devices/{device_id}", dependencies=[Depends(auth)])
    async def revoke(device_id: str):
        if not store.revoke_device(device_id):
            raise HTTPException(404, "unknown device")
        if ws := sockets.pop(device_id, None):
            try:
                await ws.send_text(json.dumps({"type": "revoked"}))
                await ws.close(code=4403)
            except Exception:
                pass
        hub.unregister(device_id)
        return {"revoked": device_id}

    # -------------------------------------------------------------- tasks
    @app.post("/tasks", status_code=202, dependencies=[Depends(auth)])
    async def submit(body: TaskIn):
        try:
            tid = orch.submit(body.device_id, body.goal, body.max_actions, body.timeout_s)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"task_id": tid}

    @app.get("/tasks", dependencies=[Depends(auth)])
    def tasks(limit: int = 50):
        return store.list_tasks(limit)

    @app.get("/tasks/{tid}", dependencies=[Depends(auth)])
    def task(tid: str):
        t = store.get_task(tid)
        if not t:
            raise HTTPException(404, "unknown task")
        return t | {"pending_approval": orch.pending_approval(tid), "transitions": store.transitions(tid)}

    @app.get("/tasks/{tid}/actions", dependencies=[Depends(auth)])
    def actions(tid: str):
        return store.list_actions(tid)

    @app.post("/tasks/{tid}/cancel", dependencies=[Depends(auth)])
    async def cancel(tid: str):
        return {"cancelled": await orch.cancel(tid)}

    @app.post("/tasks/{tid}/approve", dependencies=[Depends(auth)])
    def approve(tid: str, body: ApproveIn):
        if not orch.approve(tid, body.approved):
            raise HTTPException(409, "no pending approval for this task")
        return {"approved": body.approved}

    # ------------------------------------------------------------ recipes
    def _summary(r: dict) -> dict:
        return {"name": r["name"], "status": r["status"], "description": r.get("description", ""),
                "app": r.get("app", ""), "params": sorted(r.get("params", {})), "steps": len(r.get("steps", [])),
                "skipped_steps": r.get("skipped_steps", 0), "source_task_id": r.get("source_task_id", "")}

    @app.get("/recipes", dependencies=[Depends(auth)])
    def recipes(status: str | None = None):
        return [_summary(r) for r in store.list_recipes(status)]

    @app.get("/recipes/{name}", dependencies=[Depends(auth)])
    def recipe(name: str):
        r = store.get_recipe(name)
        if not r:
            raise HTTPException(404, "unknown recipe")
        return r | {"steps_text": describe(r), "problems": validate_recipe(r)}

    @app.post("/recipes/{name}/approve", dependencies=[Depends(auth)])
    def approve_recipe(name: str, body: RecipeApproveIn):
        r = store.get_recipe(name)
        if not r:
            raise HTTPException(404, "unknown recipe")
        try:
            if body.params:
                r = parametrize(r, body.params)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if body.description is not None:
            r["description"] = body.description
        if body.verify:
            r["verify"] = body.verify
        new = body.new_name or name
        r["name"], r["status"] = new, "approved"
        problems = validate_recipe(r)
        if problems:
            raise HTTPException(422, {"problems": problems})
        if new != name and store.get_recipe(new):
            raise HTTPException(409, f"a recipe named '{new}' already exists")
        store.save_recipe(r)
        if new != name:
            store.delete_recipe(name)
        sync_recipe_tools(get_registry(), store)
        return _summary(store.get_recipe(new)) | {"tool": f"recipe.{new}"}

    @app.post("/recipes/{name}/disable", dependencies=[Depends(auth)])
    def disable_recipe(name: str):
        if not store.set_recipe_status(name, "disabled"):
            raise HTTPException(404, "unknown recipe")
        sync_recipe_tools(get_registry(), store)
        return {"disabled": name}

    @app.delete("/recipes/{name}", dependencies=[Depends(auth)])
    def delete_recipe(name: str):
        if not store.delete_recipe(name):
            raise HTTPException(404, "unknown recipe")
        sync_recipe_tools(get_registry(), store)
        return {"deleted": name}

    @app.post("/recipes/{name}/run", status_code=202, dependencies=[Depends(auth)])
    async def run_recipe(name: str, body: RecipeRunIn):
        try:
            tid = orch.submit_recipe(body.device_id, name, body.args, body.timeout_s)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"task_id": tid}

    @app.post("/estop", dependencies=[Depends(auth)])
    async def estop():
        await orch.emergency_stop()
        return {"stopped": True}

    @app.get("/metrics", dependencies=[Depends(auth)])
    def metrics():
        out: dict = {}
        for name in ("model_latency", "screenshot_latency", "action_duration",
                     "verification_latency", "task_total"):
            v = sorted(m["value_ms"] for m in store.metrics(name))
            if v:
                out[name] = {"n": len(v), "median_ms": statistics.median(v),
                             "p95_ms": v[min(len(v) - 1, int(0.95 * len(v)))]}
        return out

    # ----------------------------------------------------------- agent WS
    @app.websocket("/agent/ws")
    async def agent_ws(ws: WebSocket):
        await ws.accept()
        device_id = None
        conn: WebSocketConnection | None = None
        try:
            hello = json.loads(await ws.receive_text())
            device_id = str(hello.get("device_id", ""))
            if hello.get("type") != "hello" or not store.authenticate_device(device_id, str(hello.get("token", ""))):
                await ws.close(code=4401)
                return
            store.touch_device(device_id, hello.get("capabilities"))
            conn = WebSocketConnection(ws.send_text)
            if old := sockets.get(device_id):     # newer connection replaces a stale one
                try:
                    await old.close(code=4000)
                except Exception:
                    pass
            sockets[device_id] = ws
            hub.register(device_id, conn)
            await ws.send_text(json.dumps({"type": "welcome"}))
            while True:
                msg = json.loads(await ws.receive_text())
                if not store.device_known(device_id):    # revoked mid-session
                    await ws.close(code=4403)
                    break
                if msg.get("type") == "heartbeat":
                    store.touch_device(device_id)
                else:
                    conn.on_message(msg)
        except (WebSocketDisconnect, json.JSONDecodeError, RuntimeError):
            pass
        finally:
            if conn:
                conn.close()
                hub.unregister(device_id, conn)
                if sockets.get(device_id) is ws:
                    sockets.pop(device_id, None)

    install_client_api(app, store, hub, orch, auth)      # pairing, /v1 for the Pilot app, and /app itself
    return app


def main() -> None:
    import argparse
    import uvicorn
    from .hosted import make_runtime

    ap = argparse.ArgumentParser(prog="taskpilot-server")
    ap.add_argument("--db", default=os.environ.get("TASKPILOT_DB", "taskpilot.db"),
                    help="SQLite path, or a mongodb:// URI (prefer the TASKPILOT_DB env var: URIs hold passwords)")
    ap.add_argument("--mongo-db", default="taskpilot", help="database name when --db is a mongodb:// URI")
    ap.add_argument("--policy", default="server_policy.json")
    ap.add_argument("--model", default=os.environ.get("TASKPILOT_MODEL", "qwen2.5vl:7b"))
    ap.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--text-model", default=os.environ.get("TASKPILOT_TEXT_MODEL"),
                    help="two-model mode: the large text model that decides every step (needs --vision-model too)")
    ap.add_argument("--vision-model", default=os.environ.get("TASKPILOT_VISION_MODEL"),
                    help="two-model mode: the model that looks at screenshots")
    ap.add_argument("--split-mode", choices=["describe", "route"], default=os.environ.get("TASKPILOT_SPLIT_MODE", "describe"))
    ap.add_argument("--base-url", default="https://integrate.api.nvidia.com/v1")
    ap.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    ap.add_argument("--allow-cloud-images", action="store_true", default=os.environ.get("TASKPILOT_ALLOW_IMAGES") == "1",
                    help="permit screenshots to be sent to the hosted API (off = text only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--demo", action="store_true",
                    help="simulated laptop + scripted model: try the app with no real desktop, no API key, no risk")
    ap.add_argument("--record-recipes", action="store_true",
                    help="save a DRAFT recipe from each successful task (drafts hold typed text; need approval)")
    ap.add_argument("--debug-logging", action="store_true",
                    help="retain typed text and command output in logs (off by default)")
    args = ap.parse_args()
    key = os.environ.get("TASKPILOT_ADMIN_KEY") or secrets.token_urlsafe(24)
    if "TASKPILOT_ADMIN_KEY" not in os.environ:
        print(f"TASKPILOT_ADMIN_KEY not set; generated for this run: {key}")
    store = open_store(args.db, debug_logging=args.debug_logging, mongo_db=args.mongo_db)
    hub = DeviceHub()
    if args.demo:
        from .demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
        policy, model = demo_policy(), DemoModel()
        if not store.device_exists(DEMO_DEVICE):
            store.enroll_device(DEMO_DEVICE, "Demo laptop (simulated)")
        hub.register(DEMO_DEVICE, DemoConnection(policy))
    else:
        with open(args.policy, encoding="utf-8") as f:
            policy = Policy.from_dict(json.load(f))
        if args.text_model and args.vision_model:
            from .hosted import make_split_runtime
            model = make_split_runtime(args.text_model, args.vision_model, base_url=args.base_url,
                                       api_key_env=args.api_key_env, mode=args.split_mode)
            print(f"two-model mode ({args.split_mode}): decides with {args.text_model}, sees with {args.vision_model}")
        else:
            model = make_runtime(args.provider, args.model, base_url=args.base_url, api_key_env=args.api_key_env,
                                 allow_cloud_images=args.allow_cloud_images)

    orch = Orchestrator(store, hub, model, policy, Config(record_recipes=args.record_recipes))
    app = create_app(store, hub, orch, key)
    shown = "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
    print(f"\nPilot app:  http://{shown}:{args.port}/app/   (on other devices use this machine's Tailscale address)")
    print(f"Pairing code for a new device (valid 10 min): {app.state.pairing.create()}\n")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
