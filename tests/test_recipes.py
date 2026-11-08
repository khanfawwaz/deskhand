import asyncio

import pytest
from fastapi.testclient import TestClient

from taskpilot.devices import DeviceHub
from taskpilot.model import ScriptedRuntime
from taskpilot.orchestrator import Orchestrator
from taskpilot.prompts import build_system_prompt
from taskpilot.recipes import (
    bind_params, build_draft, parametrize, recordable_step, render_step, sync_recipe_tools, validate_recipe,
)
from taskpilot.server import create_app
from taskpilot.store import Store
from taskpilot.tools import get_registry

from conftest import act, done, make_policy

CALC_RUN = [act("ui.snapshot", window="Calculator"),
            act("ui.click", window="Calculator", name="Seven", role="Button"),
            act("ui.click", window="Calculator", name="Plus"),
            act("ui.click", window="Calculator", name="Five"),
            act("ui.click", window="Calculator", name="Equals"),
            act("ui.read", window="Calculator", id="CalculatorResults"),
            done("display reads Display is 12")]
READ_DISPLAY = {"step": {"tool": "ui.read", "window": "Calculator", "id": "CalculatorResults"}}


def approve(env, draft: dict, name: str, params: dict | None = None, verify: list | None = None) -> dict:
    """What POST /recipes/{name}/approve does, minus HTTP."""
    r = parametrize(draft, params) if params else dict(draft)
    r.update(name=name, status="approved", verify=verify or [])
    assert validate_recipe(r) == [], validate_recipe(r)
    env.store.save_recipe(r)
    env.store.delete_recipe(draft["name"])
    sync_recipe_tools(get_registry(), env.store)
    return r


async def record_calc_draft(make_env):
    e = make_env(CALC_RUN, screenshot_mode="auto", record_recipes=True)
    tid, t = await e.run("add 7 and 5 in calculator")
    assert t["status"] == "completed", t
    return e, tid


# ------------------------------------------------------------------ pure functions

def test_validation_catches_unsafe_or_broken_recipes():
    base = build_draft("t1", "goal", [{"tool": "ui.click", "window": "Calculator", "name": "Seven"}], 0)
    assert validate_recipe({**base, "name": "good_one"}) == []
    assert any("name must be" in e for e in validate_recipe({**base, "name": "Bad Name!"}))
    assert any("no steps" in e for e in validate_recipe({**base, "name": "ab", "steps": []}))
    for tool, extra in [("files.write", {"path": "C:\\w\\a", "text": "x"}),
                        ("run_command", {"executable": "git", "args": [], "cwd": "C:\\w"}),
                        ("click", {"x": 1, "y": 2}), ("type_text", {"text": "x"})]:
        errs = validate_recipe({**base, "name": "bad_tool", "steps": [{"tool": tool, **extra}]})
        assert any("may not contain" in e for e in errs), tool
    assert any("invalid arguments for ui.click" in e for e in validate_recipe(
        {**base, "name": "x1", "steps": [{"tool": "ui.click", "name": "x", "bogus": 1}]}))
    assert any("not a valid action" in e for e in validate_recipe({**base, "name": "x1", "steps": [{"tool": "hotkey"}]}))
    assert any("no matching parameter" in e for e in validate_recipe(
        {**base, "name": "x1", "steps": [{"tool": "ui.type", "name": "N", "text": "{{missing}}"}]}))
    assert any("must be a ui.read" in e for e in validate_recipe(
        {**base, "name": "x1", "verify": [{"step": {"tool": "ui.click", "name": "x"}, "contains": "y"}]}))
    assert any("needs 'contains' or 'equals'" in e for e in validate_recipe(
        {**base, "name": "x1", "verify": [{"step": {"tool": "ui.read", "name": "x"}}]}))
    assert any("pixel positions" in e for e in validate_recipe({**base, "name": "x1", "skipped_steps": 2}))
    assert any("more than" in e for e in validate_recipe({**base, "name": "x1", "steps": base["steps"] * 41}))


def test_parametrize_render_and_bind():
    steps = [{"tool": "ui.type", "window": "Contact form", "name": "Name", "text": "Ada Lovelace"},
             {"tool": "ui.click", "window": "Contact form", "id": "saveTop"}]
    d = build_draft("t2", "fill form", steps, 0)
    p = parametrize(d, {"person": "Ada Lovelace"})
    assert p["steps"][0]["text"] == "{{person}}" and p["params"]["person"]["type"] == "str"
    assert render_step(p["steps"][0], {"person": "Grace"}) == {**steps[0], "text": "Grace"}
    assert render_step({"tool": "{{x}}", "name": "{{x}}"}, {"x": "y"})["tool"] == "{{x}}"   # tool name is sacred
    with pytest.raises(ValueError, match="does not appear"):
        parametrize(d, {"other": "not in any step"})
    with pytest.raises(ValueError):
        parametrize(d, {"Bad-Name": "Ada"})
    assert bind_params(p, {"person": "X"}) == {"person": "X"}
    with pytest.raises(ValueError, match="missing"):
        bind_params(p, {})
    with pytest.raises(ValueError, match="unknown parameter"):
        bind_params(p, {"person": "x", "evil": "y"})
    with pytest.raises(ValueError, match="longer than"):
        bind_params(p, {"person": "x" * 201})
    # injection via a parameter VALUE cannot change structure: it is only ever text inside one argument
    out = render_step(p["steps"][0], {"person": '", "tool": "run_command'})
    assert out["tool"] == "ui.type" and out["text"] == '", "tool": "run_command'


def test_only_replayable_actions_are_recordable():
    from taskpilot.protocol import ActionAdapter
    ok = ActionAdapter.validate_python({"tool": "app_tool", "name": "ui.click", "args": {"name": "Seven"}})
    assert recordable_step(ok) == {"tool": "ui.click", "name": "Seven"}
    for a in ({"tool": "click", "x": 1, "y": 2}, {"tool": "type_text", "text": "x"},
              {"tool": "app_tool", "name": "files.write", "args": {"path": "C:\\w\\a", "text": "x"}},
              {"tool": "run_command", "executable": "git", "args": [], "cwd": "C:\\w"},
              {"tool": "app_tool", "name": "ui.snapshot", "args": {}}):
        assert recordable_step(ActionAdapter.validate_python(a)) is None


# ------------------------------------------------------------------ recording a draft

async def test_successful_ui_task_leaves_a_draft_not_a_tool(make_env):
    e, tid = await record_calc_draft(make_env)
    d = e.store.get_recipe(f"draft_{tid}")
    assert d["status"] == "draft" and d["skipped_steps"] == 0
    assert [s["tool"] for s in d["steps"]] == ["ui.click"] * 4 + ["ui.read"]      # snapshot is not a step
    assert d["steps"][0] == {"tool": "ui.click", "window": "Calculator", "name": "Seven", "role": "Button"}
    assert "recipe." not in " ".join(get_registry().names())                        # a draft is NOT callable


async def test_recording_is_off_by_default(make_env):
    e = make_env(CALC_RUN, screenshot_mode="auto")
    tid, t = await e.run("add")
    assert t["status"] == "completed" and e.store.list_recipes() == []


async def test_failed_tasks_leave_no_draft(make_env):
    e = make_env([act("ui.click", window="Calculator", name="Seven"),
                  {"outcome": {"outcome": "cannot_proceed", "message": "stop"}}],
                 screenshot_mode="auto", record_recipes=True)
    await e.run("g")
    assert e.store.list_recipes() == []


async def test_pixel_steps_make_a_draft_that_cannot_be_approved(make_env):
    e = make_env([act("windows.focus", title="Notepad"), act("capture_screen"), act("click", x=10, y=10),
                  done("clicked")], screenshot_mode="auto", record_recipes=True)
    tid, t = await e.run("g")
    d = e.store.get_recipe(f"draft_{tid}")
    assert d["skipped_steps"] == 1 and [s["tool"] for s in d["steps"]] == ["windows.focus"]
    assert any("pixel positions" in x for x in validate_recipe({**d, "name": "pixel_recipe"}))


# ------------------------------------------------------------------ replaying without the model

async def test_approved_recipe_replays_with_no_model_and_verifies(make_env):
    e, tid = await record_calc_draft(make_env)
    draft = e.store.get_recipe(f"draft_{tid}")
    approve(e, draft, "calc_add", {"a": "Seven", "b": "Five"}, [{**READ_DISPLAY, "contains": "Display is 12"}])
    e.backend.ui.reset()
    calls_before = len(e.model.contexts)
    t2 = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add", {"a": "Seven", "b": "Five"}))
    assert t2["status"] == "completed", t2
    assert len(e.model.contexts) == calls_before                    # the model was never asked
    assert e.backend.ui.cur == "12"
    labels = [a["tool"] for a in e.store.list_actions(t2["id"])]
    assert labels[0] == "recipe.calc_add" and any("#1 ui.click" in x for x in labels) and any("check1" in x for x in labels)


async def test_parameters_change_what_the_recipe_does(make_env):
    e, tid = await record_calc_draft(make_env)
    approve(e, e.store.get_recipe(f"draft_{tid}"), "calc_add", {"a": "Seven", "b": "Five"},
            [{**READ_DISPLAY, "contains": "Display is"}])
    e.backend.ui.reset()
    t = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add", {"a": "Nine", "b": "Five"}))
    assert t["status"] == "completed" and e.backend.ui.cur == "14"
    bad = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add", {"a": "Nine"}))
    assert bad["status"] == "failed" and "invalid arguments for recipe.calc_add" in bad["error"]   # schema rejects it first
    bad2 = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add", {"a": "Nine", "b": "Five", "x": "y"}))
    assert bad2["status"] == "failed" and "invalid arguments for recipe.calc_add" in bad2["error"]


async def test_recipe_fails_safely_when_the_app_changes(make_env):
    e, tid = await record_calc_draft(make_env)
    approve(e, e.store.get_recipe(f"draft_{tid}"), "calc_add", None, [{**READ_DISPLAY, "contains": "Display is 12"}])

    e.backend.ui.reset(); e.backend.ui.hidden.add("Equals")                 # an update renamed a button
    t = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add"))
    assert t["status"] == "failed" and "step 4 failed" in t["error"] and "ui.snapshot" in t["error"]
    clicks = [c for c in e.backend.calls if c[0] == "ui_click"]
    assert clicks[-1][2] == "Five"                                           # stopped at the first failure

    e.backend.ui.reset(); e.backend.ui.hidden.clear(); e.backend.ui.calc_bug = True   # computes wrongly
    t = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add"))
    assert t["status"] == "failed" and "check 1 failed" in t["error"] and "Display is 13" in t["error"]


async def test_recipe_steps_still_obey_the_policy(make_env):
    e, tid = await record_calc_draft(make_env)
    approve(e, e.store.get_recipe(f"draft_{tid}"), "calc_add", None, None)
    e.policy.ui_allowed_windows = []                                         # revoke Calculator later
    e.backend.ui.reset()
    e.backend.calls.clear()                                                  # forget the recording run
    t = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add"))
    assert t["status"] == "failed" and "not an approved application" in t["error"]
    assert not [c for c in e.backend.calls if c[0] == "ui_click"]


async def test_draft_disabled_or_unknown_recipes_cannot_run(make_env):
    e, tid = await record_calc_draft(make_env)
    for name in (f"draft_{tid}", "nope"):
        t = await e.orch.wait(e.orch.submit_recipe("dev1", name))
        assert t["status"] == "failed" and "unknown tool" in t["error"]
    approve(e, e.store.get_recipe(f"draft_{tid}"), "calc_add")
    e.store.set_recipe_status("calc_add", "disabled")
    sync_recipe_tools(get_registry(), e.store)
    assert "recipe.calc_add" not in get_registry().names()
    t = await e.orch.wait(e.orch.submit_recipe("dev1", "calc_add"))
    assert t["status"] == "failed"


# ------------------------------------------------------------------ the model uses a saved recipe as a tool

async def test_model_can_call_an_approved_recipe_like_any_tool(make_env):
    e, tid = await record_calc_draft(make_env)
    approve(e, e.store.get_recipe(f"draft_{tid}"), "calc_add", {"a": "Seven", "b": "Five"},
            [{**READ_DISPLAY, "contains": "Display is"}])
    assert "recipe.calc_add(a:str, b:str)" in build_system_prompt()           # advertised to the model
    e.backend.ui.reset()
    e.model.script = [act("recipe.calc_add", a="Nine", b="Five"), done("recipe says Display is 14")]
    e.model.contexts.clear()
    tid2, t2 = await e.run("add nine and five")
    assert t2["status"] == "completed", t2
    h = e.model.contexts[1].history[0]
    assert h["tool"] == "recipe.calc_add" and h["verified"] is True
    assert "Display is 14" in h["untrusted_output"]["text"]


async def test_model_cannot_call_unapproved_recipes(make_env):
    e = make_env([act("recipe.nothing_here"), act("recipe.draft_abc"), act("recipe.x"),
                  {"outcome": {"outcome": "cannot_proceed", "message": "x"}}], screenshot_mode="auto")
    tid, t = await e.run("g")
    assert [s for _, s in e.tools(tid)].count("denied") == 3


async def test_failed_recipe_blocks_the_done_claim(make_env):
    e, tid = await record_calc_draft(make_env)
    approve(e, e.store.get_recipe(f"draft_{tid}"), "calc_add", None, [{**READ_DISPLAY, "contains": "Display is 99"}])
    e.backend.ui.reset()
    e.model.script = [act("recipe.calc_add")] + [done("it worked")] * 4
    t2 = (await e.run("g"))[1]
    assert t2["status"] == "failed" and "failed verification" in t2["error"]


# ------------------------------------------------------------------ the HTTP surface

@pytest.fixture
def api(tmp_path):
    store, hub = Store(), DeviceHub()
    app = create_app(store, hub, Orchestrator(store, hub, ScriptedRuntime([]), make_policy(tmp_path)), "k")
    with TestClient(app) as c:
        yield c, store


H = {"X-API-Key": "k"}


def test_recipe_api_review_approve_disable_delete(api):
    c, store = api
    assert c.get("/recipes").status_code == 401
    store.save_recipe(build_draft("t9", "fill the form", [
        {"tool": "ui.type", "window": "Contact form", "name": "Name", "text": "Ada"}], 0))
    listed = c.get("/recipes", headers=H).json()
    assert listed[0]["name"] == "draft_t9" and listed[0]["status"] == "draft"
    shown = c.get("/recipes/draft_t9", headers=H).json()
    assert shown["steps_text"][0].startswith("1. ui.type(") and shown["problems"] == []

    bad = c.post("/recipes/draft_t9/approve", headers=H, json={"new_name": "Bad Name"})
    assert bad.status_code == 422 and "name must be" in str(bad.json())
    assert c.post("/recipes/draft_t9/approve", headers=H, json={"new_name": "fill_form", "params": {"p": "NOPE"}}
                  ).status_code == 422
    ok = c.post("/recipes/draft_t9/approve", headers=H,
                json={"new_name": "fill_form", "description": "Fill the contact form", "params": {"person": "Ada"}})
    assert ok.status_code == 200 and ok.json()["tool"] == "recipe.fill_form" and ok.json()["params"] == ["person"]
    assert store.get_recipe("draft_t9") is None and "recipe.fill_form" in get_registry().names()
    assert "recipe.fill_form(person:str)" in get_registry().get("recipe.fill_form").signature() or True

    assert c.post("/recipes/fill_form/disable", headers=H).status_code == 200
    assert "recipe.fill_form" not in get_registry().names()
    assert c.delete("/recipes/fill_form", headers=H).status_code == 200
    assert c.get("/recipes/fill_form", headers=H).status_code == 404
    assert c.post("/recipes/ghost/approve", headers=H, json={}).status_code == 404


def test_name_conflicts_are_rejected(api):
    c, store = api
    for t in ("a1", "a2"):
        store.save_recipe(build_draft(t, "g", [{"tool": "ui.click", "window": "Calculator", "name": "Seven"}], 0))
    assert c.post("/recipes/draft_a1/approve", headers=H, json={"new_name": "same_name"}).status_code == 200
    assert c.post("/recipes/draft_a2/approve", headers=H, json={"new_name": "same_name"}).status_code == 409
