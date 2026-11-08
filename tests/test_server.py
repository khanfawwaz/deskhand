import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from taskpilot.devices import DeviceHub
from taskpilot.model import ScriptedRuntime
from taskpilot.orchestrator import Orchestrator
from taskpilot.server import create_app
from taskpilot.store import Store

from conftest import make_policy

H = {"X-API-Key": "k"}


@pytest.fixture
def client(tmp_path):
    store, hub = Store(), DeviceHub()
    app = create_app(store, hub, Orchestrator(store, hub, ScriptedRuntime([]), make_policy(tmp_path)), "k")
    with TestClient(app) as c:
        yield c, hub


def test_api_requires_key(client):
    c, _ = client
    assert c.get("/devices").status_code == 401
    assert c.get("/devices", headers={"X-API-Key": "wrong"}).status_code == 401


def test_enroll_connect_revoke(client):
    c, hub = client
    tok = c.post("/devices", json={"device_id": "lap"}, headers=H).json()["token"]
    with c.websocket_connect("/agent/ws") as ws:
        ws.send_json({"type": "hello", "device_id": "lap", "token": tok, "capabilities": ["click"]})
        assert ws.receive_json()["type"] == "welcome"
        assert "lap" in hub.online()
        assert c.get("/devices", headers=H).json()[0]["online"] is True
        c.delete("/devices/lap", headers=H)
        assert ws.receive_json()["type"] == "revoked"
    assert "lap" not in hub.online()
    with pytest.raises(WebSocketDisconnect):                 # revoked token rejected
        with c.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "hello", "device_id": "lap", "token": tok})
            ws.receive_json()


def test_bad_token_rejected(client):
    c, hub = client
    c.post("/devices", json={"device_id": "lap"}, headers=H)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "hello", "device_id": "lap", "token": "nope"})
            ws.receive_json()
    assert hub.online() == []


def test_submit_returns_immediately_and_unknown_device_404(client):
    c, _ = client
    assert c.post("/tasks", json={"device_id": "ghost", "goal": "x"}, headers=H).status_code == 404
    c.post("/devices", json={"device_id": "lap"}, headers=H)
    r = c.post("/tasks", json={"device_id": "lap", "goal": "x", "timeout_s": 5}, headers=H)
    assert r.status_code == 202 and r.json()["task_id"]
