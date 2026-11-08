import os

import pytest

from taskpilot.agent.executor import Executor
from taskpilot.protocol import ActionAdapter, ActionRequest
from taskpilot.tools import get_registry

from conftest import act, done, make_policy


def call(tool_name, /, **args):
    return ActionAdapter.validate_python({"tool": "app_tool", "name": tool_name, "args": args})


async def run(env, tool_name, /, **args):
    return await env.executor.handle(ActionRequest(action_id=os.urandom(4).hex(), task_id="t",
                                                   action=call(tool_name, **args)))


# ------------------------------------------------------------------ which windows may be automated

@pytest.mark.parametrize("title,proc,ok", [
    ("Calculator", "ApplicationFrameHost.exe", True),         # UWP: the TITLE identifies it, not the process
    ("Untitled - Notepad", "Notepad.exe", True),              # approved app id + exe
    ("Contact form", "formapp.exe", True),                    # ui_allowed_windows
    ("Other app", "other.exe", False),                        # not approved
    ("Command Prompt", "cmd.exe", False),                     # shell: would bypass the command allowlist
    ("Calculator", "cmd.exe", False),                         # deny list beats a friendly title
    ("Windows PowerShell", "powershell.exe", False),
    ("Task Manager", "Taskmgr.exe", False),
    ("User Account Control", "consent.exe", False),
    ("Windows Security", "SecHealthUI.exe", False),
    ("1Password", "1Password.exe", False),
])
def test_window_gate(tmp_path, title, proc, ok):
    assert make_policy(tmp_path).ui_window_allowed(title, proc).allowed is ok


# ------------------------------------------------------------------ snapshot / read / click

async def test_snapshot_lists_controls_by_name(make_env):
    e = make_env([])
    r = await run(e, "ui.snapshot", window="Calculator")
    assert r.ok and 'Button "Seven" id=num7Button' in r.data["tree"] and r.data["count"] > 10
    assert 'Text "Display is 0" id=CalculatorResults' in r.data["tree"]
    small = await run(e, "ui.snapshot", window="Calculator", max_elements=5)
    assert small.data["shown"] == 5 and small.data["truncated"] is True
    q = await run(e, "ui.snapshot", window="Calculator", query="seven")
    assert q.data["count"] == 1
    fg = await run(e, "ui.snapshot")                          # empty window = the foreground window
    assert fg.ok and fg.data["window"]["title"] == "Calculator"


async def test_unapproved_and_protected_windows_are_refused(make_env):
    e = make_env([])
    for w in ("Other app", "Command Prompt"):
        for r in (await run(e, "ui.snapshot", window=w),
                  await run(e, "ui.click", window=w, name="OK"),
                  await run(e, "ui.type", window=w, name="OK", text="rm -rf")):
            if w == "Command Prompt":                     # protected: refused for good, never offered for approval
                assert r.status == "error" and "protected" in r.error and "needs_access" not in r.data, (w, r.error)
            else:                                         # merely not approved yet: a request the user can answer
                assert r.status == "denied" and "not an approved" in r.error, (w, r.error)
                assert r.data["needs_access"]["kind"] == "window" and r.data["needs_access"]["process"] == "other.exe"
    assert e.backend.calls == []                              # nothing was ever clicked or typed
    assert "no visible window" in (await run(e, "ui.snapshot", window="Nonexistent")).error


async def test_click_sequence_computes_and_read_verifies(make_env):
    e = make_env([])
    for name in ("Seven", "Plus", "Five", "Equals"):
        r = await run(e, "ui.click", window="Calculator", name=name, role="Button")
        assert r.ok and r.data["method"] == "invoke"
    rd = await run(e, "ui.read", window="Calculator", id="CalculatorResults")
    assert rd.data["text"] == "Display is 12"
    assert get_registry().get("ui.read").verify(None, rd.data)[0]


async def test_ambiguous_and_missing_controls_give_helpful_errors(make_env):
    e = make_env([])
    amb = await run(e, "ui.click", window="Contact form", name="Save")
    assert amb.status == "error" and "2 controls match" in amb.error and "saveTop" in amb.error
    assert not [c for c in e.backend.calls if c[0] == "ui_click"]           # refused to guess
    assert (await run(e, "ui.click", window="Contact form", name="Save", nth=2)).ok
    assert (await run(e, "ui.click", window="Contact form", id="saveBottom")).ok
    miss = await run(e, "ui.click", window="Contact form", name="Nope")
    assert "ui.snapshot" in miss.error


def test_selector_needs_name_or_id_and_text_limit(tmp_path):
    p = make_policy(tmp_path)
    assert p.evaluate(call("ui.click", window="Calculator", role="Button")).verdict == "deny"
    assert p.evaluate(call("ui.click", window="Calculator", name="Seven")).allowed
    assert p.evaluate(call("ui.type", name="Name", text="x" * 501)).verdict == "deny"
    assert p.evaluate(call("ui.click", name="x", nth=0)).verdict == "deny"          # schema bound


async def test_type_is_verified_by_readback_and_passwords_are_refused(make_env):
    e = make_env([])
    r = await run(e, "ui.type", window="Contact form", name="Name", text="Ada")
    tool = get_registry().get("ui.type")
    assert r.ok and tool.verify(tool.params(name="Name", text="Ada"), r.data)[0]
    ok, why = tool.verify(tool.params(name="Name", text="Different"), r.data)
    assert not ok and "not the requested text" in why
    pw = await run(e, "ui.type", window="Contact form", name="Password", text="hunter2")
    assert pw.status == "error" and "password" in pw.error.lower()
    assert e.backend.ui.password_value == ""
    rd = await run(e, "ui.read", window="Contact form", name="Password")
    assert rd.status == "error" and "password" in rd.error.lower()


async def test_ui_wait(make_env):
    e = make_env([])
    assert (await run(e, "ui.wait", window="Calculator", name="Equals")).ok
    assert (await run(e, "ui.wait", window="Calculator", name="Equals", gone=True)).status == "error"
    e.backend.ui.hidden.add("Equals")
    assert (await run(e, "ui.wait", window="Calculator", name="Equals", gone=True)).ok


async def test_raw_input_can_be_restricted_to_approved_apps(make_env):
    from taskpilot.protocol import PressKey
    e = make_env([])
    e.policy.restrict_input_to_approved = True
    e.backend.active = "Command Prompt - cmd"
    r = await e.executor.handle(ActionRequest(action_id="a", task_id="t", action=PressKey(key="a")))
    assert r.status == "denied" and "input blocked" in r.error and e.backend.calls == []
    e.backend.active = "Untitled - Notepad"
    r = await e.executor.handle(ActionRequest(action_id="b", task_id="t", action=PressKey(key="a")))
    assert r.ok and e.backend.calls == [("press", ("a",))]


# ------------------------------------------------------------------ through the orchestrator

CALC = [act("ui.snapshot", window="Calculator"),
        act("ui.click", window="Calculator", name="Seven", role="Button"),
        act("ui.click", window="Calculator", name="Plus"),
        act("ui.click", window="Calculator", name="Five"),
        act("ui.click", window="Calculator", name="Equals"),
        act("ui.read", window="Calculator", id="CalculatorResults"),
        done("ui.read returned 'Display is 12'")]


async def test_new_app_task_needs_no_screenshot_at_all(make_env):
    e = make_env(CALC, screenshot_mode="auto")
    tid, t = await e.run("In Calculator compute 7 plus 5")
    assert t["status"] == "completed", t
    assert e.backend.ui.cur == "12"
    assert all(c.screenshot_b64 is None for c in e.model.contexts)            # never captured a screen
    snap = e.model.contexts[1].history[0]
    assert 'Button "Seven"' in snap["untrusted_output"]["tree"]               # window titles etc.: untrusted
    assert e.model.contexts[-1].history[-1]["untrusted_output"]["text"] == "Display is 12"


async def test_model_gets_actionable_error_for_a_blocked_window(make_env):
    e = make_env([act("ui.click", window="Command Prompt", name="OK"),
                  {"outcome": {"outcome": "cannot_proceed", "message": "protected window"}}], screenshot_mode="auto")
    tid, t = await e.run("g")
    h = e.model.contexts[1].history[0]
    assert h["status"] == "error" and "protected" in h["note"]


async def test_zoom_then_click_maps_back_to_real_display_coordinates(make_env):
    e = make_env([act("capture_screen"),
                  act("screen.zoom", x=100, y=50, width=200, height=100),    # in 1280-wide screenshot pixels
                  act("click", x=400, y=200, target="tiny close icon"),      # in the enlarged crop's pixels
                  done("clicked the icon after zooming")], screenshot_mode="auto")
    tid, t = await e.run("close it")
    assert t["status"] == "completed", t
    assert e.backend.zooms == [(200, 100, 400, 200)]                          # screenshot px -> display px (x2)
    zoomed = e.model.contexts[2]
    assert zoomed.screenshot_b64 == "ZOOM" and zoomed.screenshot_size == (800, 400)
    # click on the crop at (400,200): origin (200,100) + 400*0.5, 100 + 200*0.5
    assert e.backend.calls == [("click", 400, 200, "left", 1)]


async def test_zoom_and_coordinates_are_refused_without_a_screenshot(make_env):
    e = make_env([act("screen.zoom", x=1, y=1, width=100, height=100), act("click", x=5, y=5),
                  {"outcome": {"outcome": "cannot_proceed", "message": "no screen"}}], screenshot_mode="auto")
    tid, t = await e.run("g")
    assert e.backend.zooms == [] and e.backend.calls == []
    assert any("capture_screen first" in n for n in e.model.contexts[1].notes)


async def test_zoom_region_must_be_inside_the_screenshot(make_env):
    e = make_env([act("capture_screen"), act("screen.zoom", x=5000, y=10, width=100, height=100),
                  {"outcome": {"outcome": "cannot_proceed", "message": "x"}}], screenshot_mode="auto")
    tid, t = await e.run("g")
    assert e.backend.zooms == []
    assert any("outside" in n for n in e.model.contexts[2].notes)


async def test_store_apps_are_found_by_window_title_when_the_stub_exe_exits(make_env):
    """calc.exe exits immediately and the real app lives elsewhere: readiness must use the window title."""
    from taskpilot.policy import Policy
    e = make_env([act("launch_application", app_id="calc"), done("Calculator window is open")], screenshot_mode="auto")
    e.policy.approved_apps["calc"] = r"C:\Windows\System32\calc.exe"
    e.backend.launch_works = False                      # the stub process is gone, as on a real machine
    tid, t = await e.run("open calculator")
    assert t["status"] == "completed", t
    assert e.store.list_actions(tid)[0]["verification"].startswith("passed")


# ------------------------------------------------------------------ pixel clicks are a last resort

async def test_pixel_clicks_need_a_ui_probe_and_are_refused_for_apps_with_controls(make_env):
    e = make_env([act("capture_screen"), act("click", x=10, y=10),                      # never probed
                  act("ui.snapshot", window="Calculator"), act("capture_screen"),
                  act("click", x=10, y=10),                                          # probed: has controls
                  act("ui.click", window="Calculator", name="Seven"),               # the right way
                  done("pressed Seven")], screenshot_mode="auto", prefer_ui=True)
    e.backend.active = "Calculator"
    tid, t = await e.run("press seven")
    assert t["status"] == "completed", t
    notes = " | ".join(e.model.contexts[-1].notes)
    assert "first call ui.snapshot" in notes and "exposes controls" in notes
    assert [c for c in e.backend.calls if c[0] == "click"] == []                       # no pixel click ever landed
    assert e.backend.ui.cur == "7"


async def test_pixel_clicks_are_allowed_when_the_app_exposes_nothing(make_env):
    e = make_env([act("ui.snapshot", window="Canvas app"), act("capture_screen"), act("click", x=10, y=10),
                  done("clicked on the canvas")], screenshot_mode="auto", prefer_ui=True)
    e.policy.ui_allowed_windows.append("Canvas app")
    e.backend.active = "Canvas app"
    tid, t = await e.run("click the canvas")
    assert t["status"] == "completed", t
    assert e.backend.calls == [("click", 20, 20, "left", 1)]


async def test_pixel_clicks_are_refused_for_windows_not_approved_for_automation(make_env):
    e = make_env([act("ui.snapshot", window="Other app"), act("capture_screen"), act("click", x=5, y=5),
                  {"outcome": {"outcome": "cannot_proceed", "message": "blocked"}}],
                 screenshot_mode="auto", prefer_ui=True)
    e.backend.active = "Other app"
    tid, t = await e.run("g")
    assert e.backend.calls == [] and any("not approved for automation" in n for n in e.model.contexts[-1].notes)


def test_recipe_tool_describes_its_parameters():
    from taskpilot.recipes import recipe_tool
    t = recipe_tool({"name": "calc_add", "description": "Add two numbers", "steps": [],
                     "params": {"a": {"type": "str", "description": "e.g. Seven"}, "b": {"type": "str", "description": "e.g. Five"}}})
    assert "a: e.g. Seven" in t.summary and "b: e.g. Five" in t.summary and "same form" in t.summary


def test_irreversible_controls_need_approval(tmp_path):
    p = make_policy(tmp_path)
    for name in ("Send", "send", "Delete", "Submit", "Buy now"):
        assert p.evaluate(call("ui.click", window="Contact form", name=name)).verdict == "confirm", name
    assert p.evaluate(call("ui.click", window="Contact form", name="Save")).allowed
    assert p.evaluate(call("ui.click", window="Contact form", id="sendBtn")).allowed      # by id: name unknown
    p.ui_confirm_names = []
    assert p.evaluate(call("ui.click", window="Contact form", name="Send")).allowed


async def test_click_picks_the_one_clickable_control_when_text_shares_its_name(make_env):
    """Web pages expose a link plus the text inside it; clicking 'Privacy policy' must hit the link."""
    e = make_env([])
    r = await run(e, "ui.click", window="Contact form", name="Privacy policy")
    assert r.ok and "Hyperlink" in r.data["element"]
    assert e.backend.calls[-1][-1] == "Hyperlink"
    # reading is not narrowed: it really is ambiguous which one you mean
    rd = await run(e, "ui.read", window="Contact form", name="Privacy policy")
    assert rd.status == "error" and "3 controls match" in rd.error
    # nth applies to the clickable candidates
    assert (await run(e, "ui.click", window="Contact form", name="Privacy policy", role="Hyperlink")).ok


async def test_two_clickable_matches_are_still_refused(make_env):
    e = make_env([])
    r = await run(e, "ui.click", window="Contact form", name="Save")
    assert r.status == "error" and "2 controls match" in r.error and "role" in r.error


def test_choose_prefers_editable_controls_for_typing():
    from taskpilot.agent import uia
    cands = [{"role": "Text", "name": "Name", "id": ""}, {"role": "Edit", "name": "Name", "id": "nameBox"}]
    el, err = uia.choose(cands, {"name": "Name"}, "type", describe=lambda c: c["role"])
    assert err is None and el["role"] == "Edit"
    el, err = uia.choose(cands, {"name": "Name"}, "read", describe=lambda c: c["role"])
    assert el is None and "2 controls match" in err["error"]
