"""A person's own data: export, delete, and the retention schedule. Same contract for SQLite and MongoDB."""
import asyncio
import json
import time

import mongomock
import pytest
from fastapi.testclient import TestClient

from taskpilot import datarights
from taskpilot.demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.states import TaskState
from taskpilot.store import SqliteStore, Store
from taskpilot.store_mongo import MongoStore

ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture(params=["sqlite", "mongomock"])
def store(request):
    yield SqliteStore() if request.param == "sqlite" else MongoStore("mongodb://unused", "t", client=mongomock.MongoClient(tz_aware=True))


def fill(store, wid, tag, finish=True):
    """One workspace with a bit of everything."""
    token = store.enroll_device("dev-" + tag, "Laptop " + tag, workspace=wid)
    cid, ctoken = store.create_client("Phone " + tag, workspace=wid)
    tid = store.create_task("dev-" + tag, "goal " + tag)
    store.record_action("a-" + tag, tid, 1, "web.open", {"url": "https://example.com", "text": "secret typed text"}, "completed")
    store.add_metric(tid, "task_total", 12.0)
    if finish:
        store.transition(tid, TaskState.RUNNING, "go")
        store.transition(tid, TaskState.COMPLETED, "done", result="all done " + tag)
    store.add_experience({"goal": "goal " + tag, "created_at": time.time()}, workspace=wid)
    store.save_skill(wid, "site-" + tag, {"title": "Site " + tag, "updated_at": time.time()})
    store.add_usage({"ts": time.time(), "workspace": wid, "task_id": tid, "model": "m", "kind": "decide",
                     "prompt": 10, "completion": 5, "cost": 0.1})
    return {"tid": tid, "device_token": token, "client_token": ctoken, "client_id": cid}


def test_export_has_only_my_workspace_and_never_a_token(store):
    a, b = store.create_workspace("Alice", owner_email="a@example.com"), store.create_workspace("Bob")
    ra, rb = fill(store, a, "A"), fill(store, b, "B")
    doc = store.export_workspace(a)
    text = json.dumps(doc, default=str)
    assert doc["workspace"]["id"] == a and doc["workspace"]["owner_email"] == "a@example.com"
    assert [t["goal"] for t in doc["tasks"]] == ["goal A"]
    t = doc["tasks"][0]
    assert t["result"] == "all done A" and len(t["transitions"]) == 3 and t["actions"][0]["tool"] == "web.open"
    assert [d["device_id"] for d in doc["devices"]] == ["dev-A"] and [c["name"] for c in doc["controllers"]] == ["Phone A"]
    assert len(doc["experiences"]) == 1 and len(doc["skills"]) == 1 and len(doc["usage"]) == 1
    assert "goal B" not in text and "Bob" not in text and "dev-B" not in text                       # never someone else's data
    assert "token_hash" not in text and ra["device_token"] not in text and ra["client_token"] not in text
    assert "secret typed text" not in text                                                          # typed text was never stored
    assert store.export_workspace("w_nope") == {}


def test_delete_removes_everything_of_one_workspace_and_only_that(store):
    a, b = store.create_workspace("Alice"), store.create_workspace("Bob")
    ra, rb = fill(store, a, "A"), fill(store, b, "B")
    counts = store.delete_workspace_data(a)
    assert counts["tasks"] == 1 and counts["actions"] == 1 and counts["devices"] == 1 and counts["clients"] == 1
    assert counts["experiences"] == 1 and counts["skills"] == 1 and counts["usage"] == 1 and counts["workspace"] == 1
    assert store.get_workspace(a) is None and store.get_task(ra["tid"]) is None
    assert store.list_devices(a) == [] and store.list_clients(a) == [] and store.list_skills(a) == []
    assert store.list_actions(ra["tid"]) == [] and store.metrics("task_total") != [] and len(store.metrics("task_total")) == 1
    assert store.authenticate_client(ra["client_token"]) is None
    assert not store.authenticate_device("dev-A", ra["device_token"])
    # Bob is untouched
    assert store.get_workspace(b) and store.get_task(rb["tid"]) and store.authenticate_client(rb["client_token"])
    assert len(store.list_actions(rb["tid"])) == 1 and len(store.list_skills(b)) == 1 and len(store.list_experiences(workspace=b)) == 1


def test_deleting_the_default_workspace_keeps_its_row(store):
    fill(store, "main", "M")
    counts = store.delete_workspace_data("main")
    assert counts["tasks"] == 1 and counts["workspace"] == 0
    assert store.get_workspace("main") is not None and store.list_tasks(workspace="main") == []


def test_retention_removes_finished_history_and_old_usage_but_keeps_skills_and_running_tasks(store):
    a = store.create_workspace("Alice")
    done = fill(store, a, "A")
    live = store.create_task("dev-A", "still running")
    store.transition(live, TaskState.RUNNING, "go")
    future = time.time() + 60
    gone = store.purge_older_than(future, future)
    assert gone["tasks"] == 1 and gone["actions"] == 1 and gone["usage"] == 1 and gone["experiences"] == 1
    assert store.get_task(done["tid"]) is None and store.list_actions(done["tid"]) == []
    assert store.get_task(live) is not None                                                       # unfinished work stays
    assert len(store.list_skills(a)) == 1                                                        # learned notes stay
    assert store.get_device("dev-A") is not None
    # a cut-off of 0 means "do not purge that part"
    fill(store, a, "Z")
    assert store.purge_older_than(0, 0) == {"tasks": 0, "actions": 0, "transitions": 0, "metrics": 0, "experiences": 0, "usage": 0}


def test_retention_settings_and_the_daily_loop(monkeypatch):
    monkeypatch.delenv("TASKPILOT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("TASKPILOT_USAGE_RETENTION_DAYS", raising=False)
    assert datarights.retention_days() == (90, 400)
    monkeypatch.setenv("TASKPILOT_RETENTION_DAYS", "30")
    monkeypatch.setenv("TASKPILOT_USAGE_RETENTION_DAYS", "junk")
    assert datarights.retention_days() == (30, 400)
    monkeypatch.setenv("TASKPILOT_RETENTION_DAYS", "0")
    monkeypatch.setenv("TASKPILOT_USAGE_RETENTION_DAYS", "0")
    assert datarights.purge(SqliteStore()) == {}                                                  # both off: nothing is touched
    monkeypatch.delenv("TASKPILOT_RETENTION_DAYS")
    monkeypatch.delenv("TASKPILOT_USAGE_RETENTION_DAYS")
    s = SqliteStore()
    fill(s, "main", "N")
    assert datarights.purge(s)["tasks"] == 0                                                      # fresh data is inside 90 days


async def test_the_loop_runs_at_once_and_survives_a_failure(monkeypatch):
    calls = []

    def flaky(_store):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk hiccup")
        return {}
    monkeypatch.setattr(datarights, "purge", flaky)
    task = asyncio.create_task(datarights.retention_loop(object(), every_s=0.05))
    await asyncio.sleep(0.4)
    task.cancel()
    assert len(calls) >= 2                                                                         # ran, failed, and ran again


# ------------------------------------------------------------------ over the API

@pytest.fixture
def env():
    store, hub, policy = Store(), DeviceHub(), demo_policy()
    store.enroll_device(DEMO_DEVICE, "Demo laptop (simulated)")
    hub.register(DEMO_DEVICE, DemoConnection(policy))
    orch = Orchestrator(store, hub, DemoModel(), policy, Config(settle_delay_s=0, confirm_timeout_s=10, action_timeout_s=5, ready_timeout_s=1))
    app = create_app(store, hub, orch, "admin-key")
    with TestClient(app) as c:
        yield c, store


def pair(c, name="Test phone"):
    code = c.post("/pairing", headers=ADMIN).json()["code"]
    return c.post("/v1/pair", json={"code": code, "name": name}).json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_export_endpoint_is_a_download_of_my_own_data_only(env):
    c, store = env
    token = pair(c)
    other = store.create_workspace("Other")
    store.enroll_device("dev-other", "Other laptop", workspace=other)
    store.create_task("dev-other", "somebody else's secret goal")
    r = c.get("/v1/export", headers=auth(token))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert 'attachment; filename="deskhand-data-' in r.headers["content-disposition"]
    body = r.text
    assert "somebody else's secret goal" not in body and "token_hash" not in body and token not in body
    assert r.json()["workspace"]["id"] == "main"
    assert c.get("/v1/export").status_code == 401                                                  # needs a signed-in device


def test_export_is_rate_limited(env):
    c, _ = env
    token = pair(c)
    codes = [c.get("/v1/export", headers=auth(token)).status_code for _ in range(8)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_delete_account_needs_the_exact_phrase_then_erases_everything(env):
    c, store = env
    token = pair(c)
    tid = store.create_task(DEMO_DEVICE, "delete me later")
    store.transition(tid, TaskState.RUNNING, "go")
    store.transition(tid, TaskState.COMPLETED, "done", result="ok")
    for bad in ({}, {"confirm": ""}, {"confirm": "yes"}, {"confirm": "delete everything"}):
        assert c.post("/v1/delete-account", json=bad, headers=auth(token)).status_code == 422
    assert store.get_task(tid) is not None and c.get("/v1/me", headers=auth(token)).status_code == 200   # nothing happened
    r = c.post("/v1/delete-account", json={"confirm": "  Delete   MY data "}, headers=auth(token))     # forgiving about case/spaces
    assert r.status_code == 200 and r.json()["deleted"] is True and r.json()["removed"]["tasks"] >= 1
    assert store.get_task(tid) is None
    assert c.get("/v1/me", headers=auth(token)).status_code == 401                                    # signed out everywhere
    assert c.post("/v1/delete-account", json={"confirm": "delete my data"}).status_code == 401


def test_delete_account_cannot_touch_another_workspace(env):
    c, store = env
    token = pair(c)
    other = store.create_workspace("Other")
    store.enroll_device("dev-other", "Other laptop", workspace=other)
    keep = store.create_task("dev-other", "keep me")
    assert c.post("/v1/delete-account", json={"confirm": "delete my data"}, headers=auth(token)).status_code == 200
    assert store.get_task(keep) is not None and store.get_workspace(other) is not None


def test_the_server_owner_can_erase_a_workspace_for_someone_who_cannot_sign_in(env):
    c, store = env
    other = store.create_workspace("Lost access", owner_email="gone@example.com")
    store.enroll_device("dev-gone", "Their laptop", workspace=other)
    tid = store.create_task("dev-gone", "their goal")
    assert c.delete(f"/workspaces/{other}").status_code in (401, 403)                      # needs the admin key
    assert c.delete("/workspaces/w_nope", headers=ADMIN).status_code == 404
    r = c.delete(f"/workspaces/{other}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["removed"]["tasks"] == 1 and r.json()["removed"]["workspace"] == 1
    assert store.get_task(tid) is None and store.get_workspace(other) is None
    assert store.get_workspace("main") is not None                                         # nobody else was touched
