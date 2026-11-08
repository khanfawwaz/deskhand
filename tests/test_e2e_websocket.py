"""Real agent <-> real server over a real WebSocket (fake desktop backend)."""
import asyncio
import socket

import httpx
import uvicorn

from taskpilot.agent.executor import Executor
from taskpilot.agent.main import run_agent
from taskpilot.devices import DeviceHub
from taskpilot.model import ScriptedRuntime
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.store import Store

from conftest import FakeBackend, act, done, make_policy


async def test_full_stack_notepad_task(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    policy = make_policy(tmp_path)
    store, hub = Store(), DeviceHub()
    model = ScriptedRuntime([act("launch_application", app_id="notepad"),
                             act("type_text", text="hello world"), done("Notepad shows text")])
    orch = Orchestrator(store, hub, model, policy,
                        Config(settle_delay_s=0, ready_poll_s=0.01, ready_timeout_s=1))
    server = uvicorn.Server(uvicorn.Config(create_app(store, hub, orch, "k"), port=port, log_level="error"))
    srv = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        H = {"X-API-Key": "k"}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=H) as c:
            tok = (await c.post("/devices", json={"device_id": "lap"})).json()["token"]
            backend = FakeBackend()
            agent = asyncio.create_task(run_agent(
                {"server_url": f"ws://127.0.0.1:{port}/agent/ws", "device_id": "lap", "heartbeat_s": 1},
                tok, Executor(backend, policy)))
            for _ in range(100):
                if "lap" in hub.online():
                    break
                await asyncio.sleep(0.05)
            tid = (await c.post("/tasks", json={"device_id": "lap", "goal": "type hello"})).json()["task_id"]
            for _ in range(200):
                t = (await c.get(f"/tasks/{tid}")).json()
                if t["status"] in ("completed", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.05)
            assert t["status"] == "completed", t
            assert ("type", "hello world") in backend.calls
            acts = (await c.get(f"/tasks/{tid}/actions")).json()
            assert [a["tool"] for a in acts] == ["launch_application", "type_text"]
            assert "hello world" not in acts[1]["params"]       # not persisted
            m = (await c.get("/metrics")).json()
            assert m["model_latency"]["n"] >= 3
            agent.cancel()
    finally:
        server.should_exit = True
        await srv
