"""A chat is a thread of tasks (follow-ups continue it) and can be deleted as a whole."""
import time

import mongomock
import pytest
from fastapi.testclient import TestClient

from taskpilot import chats
from taskpilot.demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.states import TaskState
from taskpilot.store import SqliteStore, Store
from taskpilot.store_mongo import MongoStore

ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture(params=["sqlite", "mongomock"])
def sstore(request):
    yield SqliteStore() if request.param == "sqlite" else MongoStore("mongodb://unused", "t", client=mongomock.MongoClient(tz_aware=True))


def finished(store, dev, goal, result="ok", cont=None, status=TaskState.COMPLETED):
    tid = store.create_task(dev, goal, 5, 60, {"continue_from": cont} if cont else {})
    store.transition(tid, TaskState.RUNNING, "go")
    if status == TaskState.COMPLETED:
        store.transition(tid, status, "done", result=result)
    else:
        store.transition(tid, status, "stopped", error=result)
    return tid


# ------------------------------------------------------------------ threads in the stores
def test_thread_ids_follow_continue_from_and_stay_inside_one_workspace(sstore):
    a, b = sstore.create_workspace("A"), sstore.create_workspace("B")
    sstore.enroll_device("da", "A laptop", workspace=a)
    sstore.enroll_device("db", "B laptop", workspace=b)
    t1 = finished(sstore, "da", "first")
    t2 = finished(sstore, "da", "second", cont=t1)
    t3 = finished(sstore, "da", "third", cont=t2)
    other = finished(sstore, "da", "separate chat")
    foreign = finished(sstore, "db", "not yours")
    assert chats.thread_ids(sstore, a, t2) == [t1, t2, t3]                      # any task finds the whole chat
    assert chats.thread_ids(sstore, a, other) == [other]
    assert chats.thread_ids(sstore, a, foreign) == []                           # another workspace's task does not exist for A
    assert chats.thread_root(sstore, sstore.get_task(t3)) == t1


def test_a_task_whose_parent_is_gone_starts_its_own_chat_and_never_breaks(sstore):
    w = sstore.create_workspace("W")
    sstore.enroll_device("d", "L", workspace=w)
    t1 = finished(sstore, "d", "first")
    t2 = finished(sstore, "d", "second", cont=t1)
    assert sstore.delete_tasks([t1]) == 1
    assert chats.thread_ids(sstore, w, t2) == [t2]
    assert chats.history_block(sstore, sstore.get_task(t2)) == ""               # nothing to show, no error
    assert chats.thread_root(sstore, sstore.get_task(t2)) == t2


def test_delete_tasks_removes_steps_history_and_measurements(sstore):
    w = sstore.create_workspace("W")
    sstore.enroll_device("d", "L", workspace=w)
    t1 = finished(sstore, "d", "first")
    t2 = finished(sstore, "d", "keep me")
    sstore.record_action("a1", t1, 1, "web.open", {"url": "https://example.com"}, "completed")
    sstore.add_metric(t1, "task_total", 5.0)
    assert sstore.delete_tasks([t1, t1, "nope"]) == 1                           # repeats and unknown ids are harmless
    assert sstore.get_task(t1) is None and sstore.list_actions(t1) == [] and sstore.transitions(t1) == []
    assert sstore.get_task(t2) is not None and sstore.delete_tasks([]) == 0
    assert not [m for m in sstore.metrics("task_total") if m.get("task_id") == t1]


def test_history_block_names_what_was_asked_and_how_it_ended(sstore):
    w = sstore.create_workspace("W")
    sstore.enroll_device("d", "L", workspace=w)
    t1 = finished(sstore, "d", "open youtube", result="Playing now: a video " + "x" * 600)
    t2 = finished(sstore, "d", "now pause it", result="the page would not respond", cont=t1, status=TaskState.FAILED)
    t3 = sstore.create_task("d", "try again", 5, 60, {"continue_from": t2})
    text = chats.history_block(sstore, sstore.get_task(t3))
    assert text.startswith("Earlier in this conversation")
    assert text.index("open youtube") < text.index("now pause it")             # oldest first
    assert "Outcome (completed): Playing now" in text and "Outcome (failed): the page would not respond" in text
    assert "x" * 450 not in text and "…" in text                            # long answers are trimmed
    assert chats.history_block(sstore, sstore.get_task(t1)) == ""


def test_only_finished_chats_are_listed_for_delete_all(sstore):
    w = sstore.create_workspace("W")
    sstore.enroll_device("d", "L", workspace=w)
    done1 = finished(sstore, "d", "done chat")
    done2 = finished(sstore, "d", "its follow-up", cont=done1)
    live_a = finished(sstore, "d", "chat with a live follow-up")
    live_b = sstore.create_task("d", "still running", 5, 60, {"continue_from": live_a})
    groups = chats.finished_chats(sstore, w)
    assert groups == [[done1, done2]] and live_b not in sum(groups, [])


# ------------------------------------------------------------------ through the API
class SpyModel(DemoModel):
    seen: list = []

    async def propose(self, ctx):
        SpyModel.seen.append(ctx.playbook)
        return await super().propose(ctx)


@pytest.fixture
def env():
    store, hub, policy = Store(), DeviceHub(), demo_policy()
    store.enroll_device(DEMO_DEVICE, "Demo laptop (simulated)")
    hub.register(DEMO_DEVICE, DemoConnection(policy))
    SpyModel.seen = []
    orch = Orchestrator(store, hub, SpyModel(), policy, Config(settle_delay_s=0, confirm_timeout_s=10, action_timeout_s=5, ready_timeout_s=1))
    with TestClient(create_app(store, hub, orch, "admin-key")) as c:
        yield c, store, orch


def pair(c, name="Test phone"):
    code = c.post("/pairing", headers=ADMIN).json()["code"]
    return c.post("/v1/pair", json={"code": code, "name": name}).json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def wait_for(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_a_follow_up_joins_the_chat_and_the_model_is_told_the_earlier_turns(env):
    c, store, _ = env
    h = auth(pair(c))
    t1 = finished(store, DEMO_DEVICE, "open youtube", result="Playing now: a video")
    r = c.post("/v1/tasks", json={"goal": "now close it", "device_id": DEMO_DEVICE, "continue_from": t1}, headers=h)
    assert r.status_code == 202
    t2 = r.json()["task_id"]
    v = c.get(f"/v1/tasks/{t2}", headers=h).json()
    assert v["continue_from"] == t1 and v["thread"] == t1                                  # the app can rebuild the chat
    assert c.get(f"/v1/tasks/{t1}", headers=h).json()["thread"] == t1
    assert wait_for(lambda: SpyModel.seen)
    assert "Earlier in this conversation" in SpyModel.seen[0] and "open youtube" in SpyModel.seen[0] and "Playing now" in SpyModel.seen[0]


def test_a_plain_message_is_a_new_chat_and_unknown_or_foreign_ids_are_ignored(env):
    c, store, _ = env
    h = auth(pair(c))
    other = store.create_workspace("Other")
    store.enroll_device("dev-other", "Other laptop", workspace=other)
    foreign = finished(store, "dev-other", "somebody else's chat", result="secret result")
    for cf in (None, "no-such-task", foreign):
        tid = c.post("/v1/tasks", json={"goal": "open notepad", "device_id": DEMO_DEVICE, **({"continue_from": cf} if cf else {})}, headers=h).json()["task_id"]
        v = c.get(f"/v1/tasks/{tid}", headers=h).json()
        assert v["continue_from"] is None and v["thread"] == tid
    assert wait_for(lambda: len(SpyModel.seen) >= 3)
    assert all("Earlier in this conversation" not in s and "secret result" not in s for s in SpyModel.seen)


def test_a_saved_preference_reply_stays_in_the_chat(env):
    c, store, _ = env
    h = auth(pair(c))
    t1 = finished(store, DEMO_DEVICE, "open youtube", result="done")
    tid = c.post("/v1/tasks", json={"goal": "remember that I prefer dark mode", "device_id": DEMO_DEVICE, "continue_from": t1}, headers=h).json()["task_id"]
    v = c.get(f"/v1/tasks/{tid}", headers=h).json()
    assert v["status"] == "completed" and v["result"].startswith("Saved as a preference") and v["continue_from"] == t1


def test_delete_chat_removes_the_whole_thread_and_only_mine(env):
    c, store, _ = env
    h = auth(pair(c))
    t1 = finished(store, DEMO_DEVICE, "first")
    t2 = finished(store, DEMO_DEVICE, "second", cont=t1)
    keep = finished(store, DEMO_DEVICE, "another chat")
    store.record_action("a1", t1, 1, "web.open", {"url": "https://example.com"}, "completed")
    other = store.create_workspace("Other")
    store.enroll_device("dev-other", "Other laptop", workspace=other)
    foreign = finished(store, "dev-other", "not mine")
    assert c.delete(f"/v1/chats/{t2}").status_code == 401                                   # needs a signed-in device
    assert c.delete(f"/v1/chats/{foreign}", headers=h).status_code == 404                   # another workspace looks absent
    assert c.delete("/v1/chats/no-such", headers=h).status_code == 404
    assert store.get_task(foreign) is not None
    r = c.delete(f"/v1/chats/{t2}", headers=h)                                              # any task of the thread works
    assert r.status_code == 200 and r.json()["deleted"] == 2 and r.json()["ids"] == [t1, t2]
    assert store.get_task(t1) is None and store.get_task(t2) is None and store.list_actions(t1) == []
    assert store.get_task(keep) is not None and store.get_task(foreign) is not None
    assert c.get(f"/v1/tasks/{t1}", headers=h).status_code == 404


def test_delete_chat_refuses_while_something_is_running(env):
    c, store, _ = env
    h = auth(pair(c))
    t1 = finished(store, DEMO_DEVICE, "first")
    live = store.create_task(DEMO_DEVICE, "running follow-up", 5, 60, {"continue_from": t1})        # queued = not finished
    r = c.delete(f"/v1/chats/{t1}", headers=h)
    assert r.status_code == 409 and "Stop" in r.json()["detail"]
    assert store.get_task(t1) is not None and store.get_task(live) is not None


def test_delete_all_chats_leaves_running_ones_and_other_workspaces(env):
    c, store, _ = env
    h = auth(pair(c))
    d1 = finished(store, DEMO_DEVICE, "old one")
    d2 = finished(store, DEMO_DEVICE, "old follow-up", cont=d1)
    d3 = finished(store, DEMO_DEVICE, "old failed", status=TaskState.FAILED)
    live_root = finished(store, DEMO_DEVICE, "has a live follow-up")
    live = store.create_task(DEMO_DEVICE, "still running", 5, 60, {"continue_from": live_root})
    other = store.create_workspace("Other")
    store.enroll_device("dev-other", "Other laptop", workspace=other)
    foreign = finished(store, "dev-other", "not mine")
    r = c.delete("/v1/chats", headers=h)
    assert r.status_code == 200 and r.json()["deleted"] == 3
    assert all(store.get_task(i) is None for i in (d1, d2, d3))
    assert all(store.get_task(i) is not None for i in (live_root, live, foreign))
    assert c.delete("/v1/chats").status_code == 401
