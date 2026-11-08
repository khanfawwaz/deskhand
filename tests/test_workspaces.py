"""Workspaces: several people on one server, fully separate. Every test here is an attack or a leak check."""
import sqlite3
import time

import mongomock
import pytest

from taskpilot.demo import DEMO_DEVICE, DemoConnection, demo_policy
from taskpilot.store import SqliteStore
from taskpilot.store_mongo import MongoStore
from test_client_api import ADMIN, auth, env, pair, wait_for  # noqa: F401


def friend(c, name="Sam's phone", workspace=None, note=""):
    """The server owner makes an invite; the friend redeems it in the app and gets a brand-new private workspace."""
    code = c.post("/invites", headers=ADMIN).json()["code"]
    body = {"code": code, "name": name} | ({"workspace": workspace} if workspace else {})
    r = c.post("/v1/pair", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def add_laptop(c, hub, token, name="sam-pc"):
    """Add a laptop to the caller's workspace through the one-line install flow, and bring it online."""
    code = c.post("/v1/add-device", headers=auth(token)).json()["code"]
    d = c.post("/agent/join", json={"code": code, "name": name}).json()
    hub.register(d["device_id"], DemoConnection(demo_policy()))
    return d["device_id"]


# ------------------------------------------------------------------ invites create separate workspaces

def test_an_invite_creates_a_new_workspace_once_and_it_starts_empty(env):
    c, store, hub, _ = env
    code = c.post("/invites", headers=ADMIN).json()["code"]
    assert c.post("/invites").status_code == 401                                   # only the server owner can invite
    r = c.post("/v1/pair", json={"code": code, "name": "Sam's phone", "workspace": "Sam's space"})
    assert r.status_code == 200 and r.json()["new_workspace"] is True
    sam = r.json()
    assert c.post("/v1/pair", json={"code": code, "name": "again"}).status_code == 403          # single use
    me = c.get("/v1/me", headers=auth(sam["token"])).json()
    assert me["workspace"]["name"] == "Sam's space" and me["workspace"]["id"] != "main"
    assert me["devices"] == []                                                      # does NOT inherit the owner's laptop
    owner = pair(c)
    assert [d["device_id"] for d in c.get("/v1/devices", headers=auth(owner["token"])).json()] == [DEMO_DEVICE]


def test_codes_are_not_interchangeable(env):
    c, store, hub, _ = env
    laptop_code = c.post("/v1/add-device", headers=auth(pair(c)["token"])).json()["code"]
    assert c.post("/v1/pair", json={"code": laptop_code, "name": "x"}).status_code == 403
    invite = c.post("/invites", headers=ADMIN).json()["code"]
    assert c.post("/agent/join", json={"code": invite, "name": "x"}).status_code == 403          # an invite can't enroll a laptop


# ------------------------------------------------------------------ nobody can see or touch another workspace

def test_another_workspace_cannot_see_list_read_cancel_or_approve_my_tasks(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    h, hs = auth(me["token"]), auth(sam["token"])
    tid = c.post("/v1/tasks", json={"goal": "Send a message to Mum saying hi"}, headers=h).json()["task_id"]
    wait_for(c, me["token"], tid, lambda t: t["pending_approval"] is not None)
    assert c.get("/v1/tasks", headers=hs).json() == []
    assert c.get(f"/v1/tasks/{tid}", headers=hs).status_code == 404
    assert c.post(f"/v1/tasks/{tid}/cancel", headers=hs).status_code == 404
    assert c.post(f"/v1/tasks/{tid}/approve", json={"approved": True}, headers=hs).status_code == 404
    still = c.get(f"/v1/tasks/{tid}", headers=h).json()
    assert still["pending_approval"] is not None and not still["terminal"]                       # untouched
    assert c.get(f"/v1/tasks/{tid}", headers=hs).json() == c.get("/v1/tasks/nope", headers=hs).json()   # same answer as 'missing'


def test_another_workspace_cannot_command_my_laptop(env):
    c, store, hub, _ = env
    sam = friend(c)
    r = c.post("/v1/tasks", json={"goal": "Open Notepad", "device_id": DEMO_DEVICE}, headers=auth(sam["token"]))
    assert r.status_code == 404
    assert store.list_tasks(10) == []                                                            # nothing was even created
    r2 = c.post("/v1/tasks", json={"goal": "Open Notepad"}, headers=auth(sam["token"]))
    assert r2.status_code == 422                                                                 # no laptop of their own yet
    assert c.delete(f"/v1/devices/{DEMO_DEVICE}", headers=auth(sam["token"])).status_code == 404
    assert store.device_known(DEMO_DEVICE)


def test_events_stream_only_carries_my_own_workspace(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    tid = c.post("/v1/tasks", json={"goal": "Send a message to Mum saying hi"}, headers=auth(me["token"])).json()["task_id"]
    wait_for(c, me["token"], tid, lambda t: t["pending_approval"] is not None)
    mine = c.get("/v1/events?once=1", headers=auth(me["token"])).text
    theirs = c.get("/v1/events?once=1", headers=auth(sam["token"])).text
    assert DEMO_DEVICE in mine and tid in mine
    assert DEMO_DEVICE not in theirs and tid not in theirs


def test_emergency_stop_only_stops_my_own_workspace(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    sam_dev = add_laptop(c, hub, sam["token"])
    a = c.post("/v1/tasks", json={"goal": "Send a message to Mum saying hi"}, headers=auth(me["token"])).json()["task_id"]
    b = c.post("/v1/tasks", json={"goal": "Send a message to Dad saying hi", "device_id": sam_dev},
               headers=auth(sam["token"])).json()["task_id"]
    wait_for(c, me["token"], a, lambda t: t["pending_approval"] is not None)
    wait_for(c, sam["token"], b, lambda t: t["pending_approval"] is not None)
    assert c.post("/v1/estop", headers=auth(sam["token"])).status_code == 200
    assert wait_for(c, sam["token"], b, lambda t: t["terminal"])["status"] == "cancelled"
    still = c.get(f"/v1/tasks/{a}", headers=auth(me["token"])).json()
    assert not still["terminal"] and still["pending_approval"] is not None                      # my task was not stopped


# ------------------------------------------------------------------ adding your own controllers and laptops

def test_a_controller_code_from_inside_the_app_joins_the_same_workspace(env):
    c, store, hub, _ = env
    sam = friend(c)
    code = c.post("/v1/pair-code", headers=auth(sam["token"])).json()["code"]
    second = c.post("/v1/pair", json={"code": code, "name": "Sam's laptop"}).json()
    assert second["workspace"] == sam["workspace"] and second["new_workspace"] is False
    assert c.post("/v1/pair", json={"code": code, "name": "again"}).status_code == 403
    names = {m["name"] for m in c.get("/v1/members", headers=auth(sam["token"])).json()}
    assert names == {"Sam's phone", "Sam's laptop"}
    assert c.post("/v1/pair-code").status_code == 401


def test_a_laptop_added_by_a_friend_belongs_to_the_friend_only(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    dev = add_laptop(c, hub, sam["token"])
    assert dev in [d["device_id"] for d in c.get("/v1/devices", headers=auth(sam["token"])).json()]
    assert dev not in [d["device_id"] for d in c.get("/v1/devices", headers=auth(me["token"])).json()]
    assert c.post("/v1/tasks", json={"goal": "x", "device_id": dev}, headers=auth(me["token"])).status_code == 404
    assert store.get_device(dev)["workspace"] == sam["workspace"]


def test_members_can_only_be_removed_within_their_own_workspace(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    my_id = me["client_id"]
    assert c.delete(f"/v1/members/{my_id}", headers=auth(sam["token"])).status_code == 404
    assert c.get("/v1/me", headers=auth(me["token"])).status_code == 200                         # still paired
    other = c.post("/v1/pair", json={"code": c.post("/v1/pair-code", headers=auth(sam["token"])).json()["code"],
                                     "name": "old phone"}).json()
    assert c.delete(f"/v1/members/{other['client_id']}", headers=auth(sam["token"])).status_code == 200
    assert c.get("/v1/me", headers=auth(other["token"])).status_code == 401


# ------------------------------------------------------------------ limits and switches

def test_daily_task_limit_is_per_workspace(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    sam_dev = add_laptop(c, hub, sam["token"])
    wid = sam["workspace"]
    assert c.patch(f"/workspaces/{wid}", json={"max_tasks_per_day": 1}, headers=ADMIN).json()["max_tasks_per_day"] == 1
    assert c.post("/v1/tasks", json={"goal": "Open Notepad"}, headers=auth(sam["token"])).status_code == 202
    assert c.post("/v1/tasks", json={"goal": "Open Notepad"}, headers=auth(sam["token"])).status_code == 429
    assert c.post("/v1/tasks", json={"goal": "Open Notepad"}, headers=auth(me["token"])).status_code == 202   # unaffected
    info = c.get("/v1/workspace", headers=auth(sam["token"])).json()
    assert info["max_tasks_per_day"] == 1 and info["tasks_24h"] == 1


def test_switching_a_workspace_off_locks_only_that_workspace(env):
    c, store, hub, _ = env
    me, sam = pair(c), friend(c)
    assert c.patch(f"/workspaces/{sam['workspace']}", json={"disabled": True}, headers=ADMIN).status_code == 200
    assert c.get("/v1/me", headers=auth(sam["token"])).status_code == 403
    assert c.get("/v1/me", headers=auth(me["token"])).status_code == 200
    c.patch(f"/workspaces/{sam['workspace']}", json={"disabled": False}, headers=ADMIN)
    assert c.get("/v1/me", headers=auth(sam["token"])).status_code == 200
    assert c.patch("/workspaces/nope", json={"disabled": True}, headers=ADMIN).status_code == 404
    assert c.patch(f"/workspaces/{sam['workspace']}", json={"disabled": True}).status_code == 401


def test_the_owner_sees_every_workspace_with_counts(env):
    c, store, hub, _ = env
    pair(c)
    sam = friend(c, workspace="Sam")
    add_laptop(c, hub, sam["token"])
    rows = {w["id"]: w for w in c.get("/workspaces", headers=ADMIN).json()}
    assert rows["main"]["laptops"] == 1 and rows[sam["workspace"]]["laptops"] == 1
    assert rows[sam["workspace"]]["controllers"] == 1 and rows[sam["workspace"]]["name"] == "Sam"
    assert c.get("/workspaces").status_code == 401


# ------------------------------------------------------------------ learned experience never crosses workspaces

@pytest.fixture(params=["sqlite", "mongomock"])
def any_store(request):
    if request.param == "sqlite":
        yield SqliteStore()
    else:
        yield MongoStore("mongodb://unused", "t", client=mongomock.MongoClient(tz_aware=True))


def test_experience_memory_is_per_workspace(any_store):
    s = any_store
    a, b = s.create_workspace("A"), s.create_workspace("B")
    s.add_experience({"goal": "open the news", "steps": ["open x"], "created_at": 1}, workspace=a)
    assert [e["goal"] for e in s.list_experiences(workspace=a)] == ["open the news"]
    assert s.list_experiences(workspace=b) == []
    assert len(s.list_experiences()) == 1                                                        # the owner's global view


def test_store_scopes_devices_tasks_and_clients_by_workspace(any_store):
    s = any_store
    a, b = s.create_workspace("A"), s.create_workspace("B", max_tasks_per_day=5)
    s.enroll_device("da", "A laptop", workspace=a)
    s.enroll_device("db", "B laptop", workspace=b)
    ta = s.create_task("da", "goal a")
    s.create_task("db", "goal b")
    assert s.get_task(ta)["workspace"] == a
    assert [t["goal"] for t in s.list_tasks(10, workspace=a)] == ["goal a"]
    assert [d["device_id"] for d in s.list_devices(workspace=b)] == ["db"]
    assert s.count_tasks_since(a, 0) == 1 and s.active_task_ids(a) == [ta]
    ca, _ = s.create_client("a phone", workspace=a)
    assert [x["client_id"] for x in s.list_clients(workspace=a)] == [ca]
    assert s.revoke_client(ca, workspace=b) is False and s.revoke_client(ca, workspace=a) is True
    assert s.get_workspace(b)["max_tasks_per_day"] == 5
    assert s.update_workspace(b, disabled=True, name="Bee") and s.get_workspace(b)["disabled"] is True
    assert {w["id"] for w in s.list_workspaces()} >= {"main", a, b}


def test_an_old_database_without_workspaces_is_upgraded_in_place(tmp_path):
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE devices(device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, name TEXT, enrolled_at REAL NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0, last_seen REAL, capabilities TEXT);
      CREATE TABLE clients(client_id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL, created_at REAL NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0, last_seen REAL);
      CREATE TABLE experiences(id TEXT PRIMARY KEY, goal TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);
      CREATE TABLE tasks(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, max_actions INTEGER NOT NULL, timeout_s REAL NOT NULL,
        permission_context TEXT NOT NULL, result TEXT, error TEXT);
      INSERT INTO devices VALUES('old-laptop','h','Old',1,0,NULL,NULL);
      INSERT INTO tasks VALUES('t1','old-laptop','old goal','completed',1,1,5,60,'{}',NULL,NULL);
    """)
    db.commit()
    db.close()
    s = SqliteStore(path)
    assert s.get_device("old-laptop")["workspace"] == "main"
    assert s.get_task("t1")["workspace"] == "main"
    assert s.get_workspace("main")["name"] == "Main"
    assert [t["id"] for t in s.list_tasks(5, workspace="main")] == ["t1"]
