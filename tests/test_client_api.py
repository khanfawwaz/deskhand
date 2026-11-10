import json
import time

import pytest
from fastapi.testclient import TestClient

from taskpilot.demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.store import Store

ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture
def env():
    store, hub, policy = Store(), DeviceHub(), demo_policy()
    store.enroll_device(DEMO_DEVICE, "Demo laptop (simulated)")
    hub.register(DEMO_DEVICE, DemoConnection(policy))
    orch = Orchestrator(store, hub, DemoModel(), policy,
                        Config(settle_delay_s=0, confirm_timeout_s=10, action_timeout_s=5, ready_timeout_s=1))
    app = create_app(store, hub, orch, "admin-key")
    with TestClient(app) as c:
        yield c, store, hub, app


def pair(c, name="Test phone"):
    code = c.post("/pairing", headers=ADMIN).json()["code"]
    r = c.post("/v1/pair", json={"code": code, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def wait_for(c, token, tid, pred, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        t = c.get(f"/v1/tasks/{tid}", headers=auth(token)).json()
        if pred(t):
            return t
        time.sleep(0.05)
    raise AssertionError(f"timed out; last view: {t}")


# ------------------------------------------------------------------ pairing and tokens

def test_pairing_flow_gives_a_working_token_and_codes_are_single_use(env):
    c, store, *_ = env
    code = c.post("/pairing", headers=ADMIN).json()["code"]
    assert len(code) == 9 and code[4] == "-"
    r = c.post("/v1/pair", json={"code": code.lower().replace("-", " "), "name": "My iPhone"})      # forgiving input
    assert r.status_code == 200 and r.json()["token"] and r.json()["name"] == "My iPhone"
    token = r.json()["token"]
    assert c.get("/v1/me", headers=auth(token)).json()["name"] == "My iPhone"
    assert c.post("/v1/pair", json={"code": code, "name": "Again"}).status_code == 403           # single use
    stored = json.dumps(store.list_clients())
    assert token not in stored and "token_hash" not in stored                                      # never stored in clear


def test_wrong_and_expired_codes_are_refused(env):
    c, _, _, app = env
    assert c.post("/v1/pair", json={"code": "WRONG-CODE", "name": "x"}).status_code == 403
    code = c.post("/pairing", headers=ADMIN).json()["code"]
    key = app.state.pairing.normalize(code)
    app.state.pairing.codes[key] = time.time() - 1                                                  # expired
    assert c.post("/v1/pair", json={"code": code, "name": "x"}).status_code == 403
    assert c.post("/v1/pair", json={"code": "ab", "name": "x"}).status_code == 422                  # too short


def test_guessing_codes_is_rate_limited_even_for_the_right_code(env):
    c, *_ = env
    good = c.post("/pairing", headers=ADMIN).json()["code"]
    for _ in range(8):
        assert c.post("/v1/pair", json={"code": "ZZZZ-ZZZZ", "name": "x"}).status_code == 403
    assert c.post("/v1/pair", json={"code": "ZZZZ-ZZZZ", "name": "x"}).status_code == 429
    assert c.post("/v1/pair", json={"code": good, "name": "x"}).status_code == 429                  # lockout holds


def test_controller_tokens_and_the_admin_key_are_separate_powers(env):
    c, *_ = env
    token = pair(c)["token"]
    for path in ("/clients", "/devices", "/tasks", "/metrics"):
        assert c.get(path, headers=auth(token)).status_code == 401, path           # a phone cannot do admin things
    assert c.post("/pairing", headers=auth(token)).status_code == 401
    assert c.post("/devices", json={"device_id": "evil"}, headers=auth(token)).status_code == 401
    for bad in ({}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"}, ADMIN):
        assert c.get("/v1/me", headers=bad).status_code == 401                      # and the admin key is not a token


def test_revoking_a_device_cuts_it_off_immediately(env):
    c, store, *_ = env
    a, b = pair(c, "Laptop A"), pair(c, "Laptop B")
    assert c.get("/clients", headers=ADMIN).json()[0]["name"] == "Laptop A"
    assert c.delete(f"/clients/{a['client_id']}", headers=ADMIN).status_code == 200
    assert c.get("/v1/me", headers=auth(a["token"])).status_code == 401
    assert c.get("/v1/me", headers=auth(b["token"])).status_code == 200
    assert c.post("/v1/unpair", headers=auth(b["token"])).status_code == 200                       # a device can leave itself
    assert c.get("/v1/me", headers=auth(b["token"])).status_code == 401
    assert c.delete("/clients/ghost", headers=ADMIN).status_code == 404


# ------------------------------------------------------------------ running tasks from the app

def test_devices_list_shows_status_but_no_secrets(env):
    c, *_ = env
    token = pair(c)["token"]
    d = c.get("/v1/devices", headers=auth(token)).json()
    assert d == [{"device_id": DEMO_DEVICE, "name": "Demo laptop (simulated)", "online": True, "last_seen": None,
                  "extension": False}]                       # a simulated laptop has no Chrome extension connected


def test_a_task_runs_end_to_end_and_the_app_sees_steps_and_the_result(env):
    c, *_ = env
    token = pair(c)["token"]
    r = c.post("/v1/tasks", json={"goal": "check the weather"}, headers=auth(token))        # device auto-chosen
    assert r.status_code == 202 and r.json()["device_id"] == DEMO_DEVICE
    t = wait_for(c, token, r.json()["task_id"], lambda t: t["terminal"])
    assert t["status"] == "completed" and "demo mode" in t["result"]
    assert [s["tool"] for s in t["steps"]] == ["windows.list", "ui.snapshot", "ui.read_page"]
    assert all(s["status"] == "succeeded" for s in t["steps"]) and t["pending_approval"] is None
    listed = c.get("/v1/tasks", headers=auth(token)).json()
    assert listed[0]["id"] == t["id"] and "steps" not in listed[0]                                  # list view is light


def test_an_irreversible_step_waits_for_the_app_and_obeys_deny_or_approve(env):
    c, *_ = env
    token = pair(c)["token"]
    h = auth(token)
    tid = c.post("/v1/tasks", json={"goal": "Send a message to Mum saying hi"}, headers=h).json()["task_id"]
    t = wait_for(c, token, tid, lambda t: t["pending_approval"] is not None)
    assert t["status"] == "waiting_for_confirmation" and t["pending_approval"]["tool"] == "ui.click"
    assert "irreversible" in t["pending_approval"]["reason"]
    assert c.post(f"/v1/tasks/{tid}/approve", json={"approved": False}, headers=h).status_code == 200
    t = wait_for(c, token, tid, lambda t: t["terminal"])
    assert t["status"] == "failed" and "declined" in (t["error"] or "")                              # denied: not sent

    tid2 = c.post("/v1/tasks", json={"goal": "Send a message to Mum saying hi"}, headers=h).json()["task_id"]
    wait_for(c, token, tid2, lambda t: t["pending_approval"] is not None)
    assert c.post(f"/v1/tasks/{tid2}/approve", json={"approved": True}, headers=h).status_code == 200
    assert wait_for(c, token, tid2, lambda t: t["terminal"])["status"] == "completed"
    assert c.post(f"/v1/tasks/{tid2}/approve", json={"approved": True}, headers=h).status_code == 409   # nothing waiting


def test_cancel_and_emergency_stop_from_the_app(env):
    c, *_ = env
    token = pair(c)["token"]
    h = auth(token)
    a = c.post("/v1/tasks", json={"goal": "Send a message to Dad"}, headers=h).json()["task_id"]
    wait_for(c, token, a, lambda t: t["pending_approval"] is not None)
    assert c.post(f"/v1/tasks/{a}/cancel", headers=h).json() == {"cancelled": True}
    assert wait_for(c, token, a, lambda t: t["terminal"])["status"] == "cancelled"
    b = c.post("/v1/tasks", json={"goal": "Send a message to Gran"}, headers=h).json()["task_id"]
    wait_for(c, token, b, lambda t: t["pending_approval"] is not None)
    assert c.post("/v1/estop", headers=h).json() == {"stopped": True}
    assert wait_for(c, token, b, lambda t: t["terminal"])["status"] in ("cancelled", "failed")


def test_task_input_is_validated(env):
    c, store, hub, _ = env
    h = auth(pair(c)["token"])
    assert c.post("/v1/tasks", json={"goal": ""}, headers=h).status_code == 422
    assert c.post("/v1/tasks", json={"goal": "x" * 4001}, headers=h).status_code == 422
    assert c.post("/v1/tasks", json={"goal": "hi", "device_id": "ghost"}, headers=h).status_code == 404
    assert c.post("/v1/tasks", json={"goal": "hi", "max_actions": 500}, headers=h).status_code == 422
    assert c.get("/v1/tasks/nope", headers=h).status_code == 404
    store.enroll_device("second-laptop", "Second")
    hub.register("second-laptop", DemoConnection(demo_policy()))
    assert c.post("/v1/tasks", json={"goal": "hi"}, headers=h).status_code == 422                   # two online: must choose


def test_tasks_remember_which_app_started_them(env):
    c, store, *_ = env
    cl = pair(c, "Kitchen tablet")
    tid = c.post("/v1/tasks", json={"goal": "check the weather"}, headers=auth(cl["token"])).json()["task_id"]
    wait_for(c, cl["token"], tid, lambda t: t["terminal"])
    assert json.loads(store._q("SELECT permission_context FROM tasks WHERE id=?", tid)[0]["permission_context"]) == {
        "client_id": cl["client_id"], "via": "app"}


# ------------------------------------------------------------------ live events

def parse_sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev = {"event": "message", "data": ""}
        for line in block.splitlines():
            if line.startswith("event:"):
                ev["event"] = line[6:].strip()
            elif line.startswith("data:"):
                ev["data"] += line[5:].strip()
        if ev["data"]:
            out.append((ev["event"], json.loads(ev["data"])))
    return out


def test_event_snapshot_lists_devices_and_current_tasks(env):
    c, *_ = env
    token = pair(c)["token"]
    tid = c.post("/v1/tasks", json={"goal": "check the weather"}, headers=auth(token)).json()["task_id"]
    wait_for(c, token, tid, lambda t: t["terminal"])
    r = c.get("/v1/events?once=1", headers=auth(token))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "devices" and "task" in kinds
    task_event = next(d for k, d in events if k == "task" and d["id"] == tid)
    assert task_event["status"] == "completed" and task_event["steps"]
    assert c.get("/v1/events?once=1").status_code == 401                                               # needs a token


# ------------------------------------------------------------------ the app itself is served safely

def test_the_app_is_served_with_security_headers_and_the_root_redirects(env):
    c, *_ = env
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
    page = c.get("/app/")
    assert page.status_code == 200 and "<title>Deskhand</title>" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp and "unsafe-eval" not in csp
    assert page.headers["x-content-type-options"] == "nosniff" and "microphone=(self)" in page.headers["permissions-policy"]
    assert c.get("/app/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert c.get("/app/sw.js").status_code == 200 and c.get("/app/icons/icon-512.png").status_code == 200
    token = pair(c)["token"]
    assert c.get("/v1/me", headers=auth(token)).headers["cache-control"] == "no-store"                 # API never cached
    assert c.get("/admin").status_code == 200                                                          # old dashboard moved
