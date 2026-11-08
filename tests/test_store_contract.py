"""Same behavioural contract for every store backend.

Set TASKPILOT_TEST_MONGO_URI to also run against a real mongod (uses a throwaway database).
"""
import json
import os
import uuid

import mongomock
import pytest

from taskpilot.states import InvalidTransition, TaskState
from taskpilot.store import SqliteStore, open_store
from taskpilot.store_mongo import MongoStore

BACKENDS = ["sqlite", "mongomock"] + (["mongo"] if os.environ.get("TASKPILOT_TEST_MONGO_URI") else [])


@pytest.fixture(params=BACKENDS)
def store(request):
    if request.param == "sqlite":
        yield SqliteStore()
    elif request.param == "mongomock":
        yield MongoStore("mongodb://unused", "t", client=mongomock.MongoClient(tz_aware=True))
    else:
        s = open_store(os.environ["TASKPILOT_TEST_MONGO_URI"], mongo_db="tp_test_" + uuid.uuid4().hex[:8])
        yield s
        s._client.drop_database(s._db.name)
        s.close()


def test_device_lifecycle(store):
    assert not store.device_exists("d") and not store.device_known("d")
    tok = store.enroll_device("d", "laptop")
    assert store.device_exists("d") and store.device_known("d")
    assert store.authenticate_device("d", tok) and not store.authenticate_device("d", "wrong")
    assert not store.authenticate_device("nobody", tok)
    assert tok not in store.get_device("d")["token_hash"]           # only a hash is stored
    store.touch_device("d", ["click", "type_text"])
    listed = store.list_devices()[0]
    assert listed["token_hash"] is None and listed["capabilities"] == ["click", "type_text"]
    assert store.revoke_device("d") is True
    assert not store.authenticate_device("d", tok) and not store.device_known("d")
    assert store.device_exists("d")                                  # revoked, not deleted
    assert store.revoke_device("ghost") is False


def test_task_creation_and_listing(store):
    store.enroll_device("d")
    a = store.create_task("d", "first", max_actions=7, timeout_s=9, permission_context={"k": 1})
    b = store.create_task("d", "second")
    t = store.get_task(a)
    assert t["id"] == a and t["status"] == "queued" and t["max_actions"] == 7
    assert "transitions" not in t and t["result"] is None and t["error"] is None
    assert [x["id"] for x in store.list_tasks()] == [b, a]           # newest first
    assert store.get_task("nope") is None


def test_transitions_guarded_recorded_and_terminal_final(store):
    store.enroll_device("d")
    t = store.create_task("d", "g")
    with pytest.raises(InvalidTransition):
        store.transition(t, TaskState.COMPLETED)                      # queued -> completed illegal
    assert store.transition(t, TaskState.RUNNING, "go")
    assert store.transition(t, TaskState.RUNNING)                     # same state: no-op True
    assert store.transition(t, TaskState.COMPLETED, "ok", result="done!")
    assert store.transition(t, TaskState.FAILED, error="late") is False   # terminal is final
    row = store.get_task(t)
    assert row["status"] == "completed" and row["result"] == "done!" and row["error"] is None
    hist = store.transitions(t)
    assert [(h["from_state"], h["to_state"]) for h in hist] == [
        (None, "queued"), ("queued", "running"), ("running", "completed")]
    assert hist[1]["note"] == "go"


def test_actions_roundtrip_ordering_and_redaction(store):
    store.enroll_device("d")
    t = store.create_task("d", "g")
    store.record_action("a2", t, 2, "click", {"x": 1, "y": 2}, "proposed", "allow", 12.5)
    store.record_action("a1", t, 1, "type_text", {"text": "hunter2"}, "proposed")
    assert [a["action_id"] for a in store.list_actions(t)] == ["a1", "a2"]
    a1 = store.get_action("a1")
    assert a1["params"] == {"text": "<redacted>"} and "hunter2" not in json.dumps(a1)
    store.update_action("a2", status="succeeded", retries=1, result={"exit_code": 0, "pid": 5},
                        verification="passed: ok", bogus_field="ignored")
    a2 = store.get_action("a2")
    assert a2["status"] == "succeeded" and a2["retries"] == 1 and a2["verification"] == "passed: ok"
    assert a2["result"] == {"exit_code": 0, "pid": 5} and a2["params"] == {"x": 1, "y": 2}
    assert a2["model_latency_ms"] == 12.5 and a2["task_id"] == t
    assert store.get_action("missing") is None


def test_registry_tool_args_are_redacted_too(store):
    store.enroll_device("d"); t = store.create_task("d", "g")
    store.record_action("a", t, 1, "files.write",
                        {"name": "files.write", "args": {"path": "C:\\w\\a.txt", "text": "secret body"}}, "proposed")
    got = store.get_action("a")["params"]
    assert got["args"] == {"path": "C:\\w\\a.txt", "text": "<redacted>"}
    assert "secret body" not in json.dumps(store.get_action("a"))


def test_debug_logging_keeps_text(store):
    store.debug_logging = True
    store.enroll_device("d"); t = store.create_task("d", "g")
    store.record_action("a", t, 1, "type_text", {"text": "visible"}, "proposed")
    assert store.get_action("a")["params"] == {"text": "visible"}


def test_metrics(store):
    store.add_metric("t1", "model_latency", 100.0)
    store.add_metric(None, "action_duration", 5.0)
    assert [m["value_ms"] for m in store.metrics("model_latency")] == [100.0]
    assert len(store.metrics()) == 2
    assert {"name", "value_ms", "at", "task_id"} <= set(store.metrics()[0])


def test_open_store_selects_backend(tmp_path):
    assert isinstance(open_store(":memory:"), SqliteStore)
    assert isinstance(open_store(str(tmp_path / "x.db")), SqliteStore)


def test_mongo_unreachable_fails_fast():
    import time
    t0 = time.time()
    with pytest.raises(Exception):
        open_store("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300")
    assert time.time() - t0 < 10


def test_recipe_crud_and_upsert(store):
    assert store.get_recipe("nope") is None and store.list_recipes() == []
    r = {"name": "calc_add", "status": "draft", "description": "add", "steps": [{"tool": "ui.click", "name": "Seven"}],
         "params": {"a": {"type": "str"}}, "verify": [], "skipped_steps": 0}
    store.save_recipe(r)
    got = store.get_recipe("calc_add")
    assert got["status"] == "draft" and got["steps"] == r["steps"] and got["params"] == r["params"]
    created = got["created_at"]
    store.save_recipe({**r, "status": "approved", "description": "changed"})           # upsert keeps created_at
    got = store.get_recipe("calc_add")
    assert got["status"] == "approved" and got["description"] == "changed" and got["created_at"] == created
    store.save_recipe({**r, "name": "other_one"})
    assert [x["name"] for x in store.list_recipes()] == ["calc_add", "other_one"]
    assert [x["name"] for x in store.list_recipes("approved")] == ["calc_add"]
    assert store.set_recipe_status("other_one", "disabled") is True
    assert store.set_recipe_status("ghost", "disabled") is False
    assert store.get_recipe("other_one")["status"] == "disabled"
    assert store.delete_recipe("calc_add") is True and store.delete_recipe("calc_add") is False
    assert [x["name"] for x in store.list_recipes()] == ["other_one"]


def test_paired_clients_lifecycle(store):
    cid, token = store.create_client("Mum's iPad")
    assert store.authenticate_client("wrong") is None and store.authenticate_client("") is None
    c = store.authenticate_client(token)
    assert c["client_id"] == cid and c["name"] == "Mum's iPad" and "token_hash" not in c and c["last_seen"] is not None
    listed = store.list_clients()
    assert [x["client_id"] for x in listed] == [cid] and "token_hash" not in listed[0] and listed[0]["revoked"] is False
    assert token not in json.dumps(listed)                                      # the token itself is never stored
    cid2, token2 = store.create_client("Phone")
    assert token2 != token and store.authenticate_client(token2)["client_id"] == cid2
    assert store.revoke_client(cid) is True and store.revoke_client("ghost") is False
    assert store.authenticate_client(token) is None and store.authenticate_client(token2) is not None
    assert [x["revoked"] for x in store.list_clients()] == [True, False]


def test_experience_memory_roundtrip_newest_first(store):
    assert store.list_experiences() == []
    a = store.add_experience({"goal": "Play the newest video", "steps": ["open youtube.com/@x/videos"], "created_at": 1.0})
    b = store.add_experience({"goal": "Read the news", "steps": ["open news.google.com"], "created_at": 2.0})
    got = store.list_experiences()
    assert [e["goal"] for e in got] == ["Read the news", "Play the newest video"]
    assert got[0]["id"] == b and got[1]["id"] == a and got[1]["steps"] == ["open youtube.com/@x/videos"]
    assert len(store.list_experiences(limit=1)) == 1
