"""The page can tell whether each computer's Chrome extension is connected, and where to get the extension."""
import pytest
from fastapi.testclient import TestClient

from taskpilot.demo import DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.store import Store

ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture
def env():
    store, hub = Store(), DeviceHub()
    orch = Orchestrator(store, hub, DemoModel(), demo_policy(),
                        Config(settle_delay_s=0, confirm_timeout_s=10, action_timeout_s=5, ready_timeout_s=1))
    app = create_app(store, hub, orch, "admin-key")
    with TestClient(app) as c:
        yield c, store


def _client_token(c):
    code = c.post("/pairing", headers=ADMIN).json()["code"]
    return c.post("/v1/pair", json={"code": code, "name": "Phone"}).json()["token"]


def _devices(c, token):
    return {d["device_id"]: d for d in c.get("/v1/devices", headers={"Authorization": f"Bearer {token}"}).json()}


def test_extension_flag_follows_hello_heartbeat_and_disconnect(env):
    c, _ = env
    tok = c.post("/devices", json={"device_id": "lap", "name": "Laptop"}, headers=ADMIN).json()["token"]
    me = _client_token(c)
    assert _devices(c, me)["lap"]["extension"] is False                      # not connected at all
    with c.websocket_connect("/agent/ws") as ws:
        ws.send_json({"type": "hello", "device_id": "lap", "token": tok, "capabilities": [], "extension": True})
        assert ws.receive_json()["type"] == "welcome"
        d = _devices(c, me)["lap"]
        assert d["online"] is True and d["extension"] is True                # told in the hello
        ws.send_json({"type": "heartbeat", "extension": False})              # the extension went away
        ws.send_json({"type": "heartbeat", "extension": False})
        assert _devices(c, me)["lap"]["extension"] is False
        ws.send_json({"type": "heartbeat", "extension": True})
        ws.send_json({"type": "heartbeat"})                                  # a beat without the field changes nothing
        assert _devices(c, me)["lap"]["extension"] is True
    d = _devices(c, me)["lap"]
    assert d["online"] is False and d["extension"] is False                  # offline computers never claim a connected extension


def test_an_old_helper_that_never_says_anything_is_not_set_up(env):
    c, _ = env
    tok = c.post("/devices", json={"device_id": "old", "name": "Old"}, headers=ADMIN).json()["token"]
    me = _client_token(c)
    with c.websocket_connect("/agent/ws") as ws:
        ws.send_json({"type": "hello", "device_id": "old", "token": tok})     # no "extension" field at all
        assert ws.receive_json()["type"] == "welcome"
        assert _devices(c, me)["old"]["extension"] is False


def test_events_stream_carries_the_flag(env):
    c, _ = env
    tok = c.post("/devices", json={"device_id": "lap", "name": "Laptop"}, headers=ADMIN).json()["token"]
    me = _client_token(c)
    with c.websocket_connect("/agent/ws") as ws:
        ws.send_json({"type": "hello", "device_id": "lap", "token": tok, "extension": True})
        ws.receive_json()
        r = c.get("/v1/events?once=1", headers={"Authorization": f"Bearer {me}"})
        assert '"extension": true' in r.text


def test_config_exposes_a_plain_https_extension_link_only(env, monkeypatch):
    c, _ = env
    monkeypatch.delenv("TASKPILOT_EXTENSION_URL", raising=False)
    assert c.get("/v1/auth/config").json()["extension_url"] == ""            # not published yet: no link
    monkeypatch.setenv("TASKPILOT_EXTENSION_URL", "https://chromewebstore.google.com/detail/deskhand/abc")
    assert c.get("/v1/auth/config").json()["extension_url"].startswith("https://chromewebstore.google.com/")
    me = _client_token(c)
    assert c.get("/v1/me", headers={"Authorization": f"Bearer {me}"}).json()["extension_url"].startswith("https://")
    for bad in ("javascript:alert(1)", "http://insecure.example", "https://x y", "data:text/html,hi"):
        monkeypatch.setenv("TASKPILOT_EXTENSION_URL", bad)
        assert c.get("/v1/auth/config").json()["extension_url"] == ""        # anything else never reaches the page
