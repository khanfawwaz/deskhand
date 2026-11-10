"""Saying "remember this / set it as a preference" saves a preference and runs NOTHING; saved preferences reach later tasks."""
import asyncio

import mongomock
import pytest
from fastapi.testclient import TestClient

from taskpilot import prefs
from taskpilot.demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.store import SqliteStore, Store
from taskpilot.store_mongo import MongoStore

ADMIN = {"X-API-Key": "admin-key"}


# ------------------------------------------------------------------ what counts as a preference
@pytest.mark.parametrize("goal,expected", [
    ("when I say play calming music play lofi music set it as preference", "When you say “play calming music”: play lofi music"),
    ("Whenever I say play calming music, play lofi music. Remember this", "When you say “play calming music”: play lofi music"),
    ("remember that I prefer dark mode on youtube", "I prefer dark mode on youtube"),
    ("from now on open netflix on the Kids profile", "Open netflix on the Kids profile"),
    ("I prefer the Alex profile on Netflix", "I prefer the Alex profile on Netflix"),
    ("next time I open YouTube play the latest video", "Next time I open YouTube play the latest video"),
])
def test_preference_sentences_are_recognised_and_tidied(goal, expected):
    assert prefs.parse_preference(goal) == {"text": expected}


@pytest.mark.parametrize("goal", [
    "play calming music", "open youtube and play lofi music", "what are my preferences?",
    "do you remember my favourite playlist?", "open settings and change the theme",
    "write a note: remember to buy milk", "show my preferences", "",
])
def test_ordinary_tasks_and_questions_are_left_alone(goal):
    assert prefs.parse_preference(goal) is None


def test_remember_this_without_saying_what_saves_nothing():
    assert prefs.parse_preference("set it as preference") == {"text": ""}
    assert prefs.parse_preference("ok remember this") == {"text": ""}


def test_the_model_block_is_short_and_labelled():
    assert prefs.render_for_model([]) == ""
    out = prefs.render_for_model([{"text": "When you say “calm”: play lofi"}] + [{"text": "x" * 400}] * 30)
    assert out.startswith("The person's saved preferences") and "play lofi" in out and len(out) < 2000


# ------------------------------------------------------------------ the stores
@pytest.fixture(params=["sqlite", "mongomock"])
def store(request):
    yield SqliteStore() if request.param == "sqlite" else MongoStore("mongodb://unused", "t", client=mongomock.MongoClient(tz_aware=True))


def test_store_keeps_preferences_per_workspace_without_duplicates(store):
    a, b = store.create_workspace("A"), store.create_workspace("B")
    p1 = store.add_preference(a, "When you say “calm”: play lofi")
    assert store.add_preference(a, "  when you say “CALM”: play lofi ") == p1            # same sentence: kept once
    store.add_preference(b, "Only B's")
    assert [p["text"] for p in store.list_preferences(a)] == ["When you say “calm”: play lofi"]
    assert store.delete_preference(b, p1) is False                                                  # not B's to delete
    assert store.delete_preference(a, p1) is True and store.list_preferences(a) == []
    assert store.list_preferences(b)


def test_the_oldest_preferences_go_when_there_are_too_many(store):
    w = store.create_workspace("W")
    for i in range(prefs.MAX_PER_WORKSPACE + 5):
        store.add_preference(w, f"preference number {i}")
    texts = [p["text"] for p in store.list_preferences(w, limit=500)]
    assert len(texts) == prefs.MAX_PER_WORKSPACE and "preference number 0" not in texts and f"preference number {prefs.MAX_PER_WORKSPACE + 4}" in texts


def test_preferences_are_in_the_export_and_erased_with_the_account(store):
    w = store.create_workspace("W", owner_email="w@example.com")
    store.add_preference(w, "I prefer the Alex profile on Netflix")
    assert [p["text"] for p in store.export_workspace(w)["preferences"]] == ["I prefer the Alex profile on Netflix"]
    assert store.delete_workspace_data(w)["preferences"] == 1 and store.list_preferences(w) == []


# ------------------------------------------------------------------ through the real API
class SpyModel(DemoModel):
    """Remembers what the model was told, so a test can check the preferences reached it."""
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


def test_saying_set_it_as_a_preference_saves_it_and_runs_nothing(env):
    c, store, _ = env
    h = auth(pair(c))
    r = c.post("/v1/tasks", json={"goal": "when I say play calming music play lofi music set it as preference", "device_id": DEMO_DEVICE}, headers=h)
    assert r.status_code == 202
    tid = r.json()["task_id"]
    t = c.get(f"/v1/tasks/{tid}", headers=h).json()
    assert t["status"] == "completed" and t["result"].startswith("Saved as a preference:") and "play lofi music" in t["result"]
    assert "I have not done anything now" in t["result"]
    assert store.list_actions(tid) == [] and SpyModel.seen == []                                   # no action, the model was never asked
    listed = c.get("/v1/preferences", headers=h).json()
    assert [p["text"] for p in listed] == ["When you say “play calming music”: play lofi music"]


def test_a_plain_request_is_still_a_task(env):
    c, store, orch = env
    h = auth(pair(c))
    r = c.post("/v1/tasks", json={"goal": "play calming music", "device_id": DEMO_DEVICE}, headers=h)
    assert r.status_code == 202
    assert c.get("/v1/preferences", headers=h).json() == []
    asyncio.run(asyncio.sleep(0))                                                                    # a real run was started
    assert store.get_task(r.json()["task_id"]) is not None


def test_remember_this_with_nothing_to_remember_gets_a_gentle_reply(env):
    c, _, _ = env
    h = auth(pair(c))
    tid = c.post("/v1/tasks", json={"goal": "set it as preference", "device_id": DEMO_DEVICE}, headers=h).json()["task_id"]
    t = c.get(f"/v1/tasks/{tid}", headers=h).json()
    assert t["status"] == "completed" and t["result"].startswith("I did not save anything")
    assert c.get("/v1/preferences", headers=h).json() == []


def test_preferences_can_be_listed_and_deleted_only_by_their_owner(env):
    c, store, _ = env
    h = auth(pair(c))
    pid = store.add_preference("main", "I prefer dark mode")
    other = store.create_workspace("Other")
    foreign = store.add_preference(other, "somebody else's")
    assert [p["id"] for p in c.get("/v1/preferences", headers=h).json()] == [pid]
    assert c.delete(f"/v1/preferences/{foreign}", headers=h).status_code == 404
    assert c.delete(f"/v1/preferences/{pid}", headers=h).json() == {"forgotten": pid}
    assert c.get("/v1/preferences", headers=h).json() == [] and store.list_preferences(other)
    assert c.get("/v1/preferences").status_code == 401


def test_saved_preferences_are_put_in_front_of_the_model_on_the_next_task(env):
    c, store, orch = env
    h = auth(pair(c))
    store.add_preference("main", "When you say “calm”: play lofi music")
    tid = c.post("/v1/tasks", json={"goal": "open notepad", "device_id": DEMO_DEVICE}, headers=h).json()["task_id"]
    asyncio.run(asyncio.wait_for(orch.wait(tid), 20)) if False else None
    for _ in range(100):                                                                             # the run is on the server's own loop
        if SpyModel.seen or (store.get_task(tid) or {}).get("status") in ("completed", "failed"):
            break
        import time
        time.sleep(0.1)
    assert SpyModel.seen and "saved preferences" in SpyModel.seen[0] and "play lofi music" in SpyModel.seen[0]
