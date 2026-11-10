"""The shortcut (Alt+F8) and the voice prompt behind it."""
import json
import re
from pathlib import Path

import httpx
import pytest

from taskpilot.agent import hotkey, voice, voice_ui
from taskpilot.agent.voice import LinkError, ServerLink, VoiceBridge


# ------------------------------------------------------------------ the shortcut
def test_the_default_shortcut_is_alt_f8():
    assert hotkey.DEFAULT == "alt+f8"
    assert hotkey.parse_hotkey("alt+f8") == (hotkey.MOD_ALT, 0x77)
    assert hotkey.describe("alt+f8") == "Alt+F8"


@pytest.mark.parametrize("text,expected", [
    ("ctrl+shift+k", (hotkey.MOD_CONTROL | hotkey.MOD_SHIFT, ord("K"))),
    ("Win + Space", (hotkey.MOD_WIN, 0x20)),
    ("CTRL+ALT+1", (hotkey.MOD_CONTROL | hotkey.MOD_ALT, ord("1"))),
    ("f12", (0, 0x7B)),                                         # a function key alone is fine
    ("alt+f24", (hotkey.MOD_ALT, 0x87)),
])
def test_shortcuts_are_understood(text, expected):
    assert hotkey.parse_hotkey(text) == expected


@pytest.mark.parametrize("bad", ["", "k", "alt", "alt+", "foo+f8", "alt+f99", "alt+banana", "ctrl+shift"])
def test_bad_shortcuts_are_refused_with_a_plain_reason(bad):
    with pytest.raises(hotkey.BadHotkey):
        hotkey.parse_hotkey(bad)


def test_the_shortcut_comes_from_the_computers_settings_file(tmp_path):
    assert hotkey.read_hotkey(tmp_path) == "alt+f8"                      # no file: the default
    (tmp_path / "agent.json").write_text(json.dumps({"hotkey": "ctrl+alt+v"}), encoding="utf-8")
    assert hotkey.read_hotkey(tmp_path) == "ctrl+alt+v"
    (tmp_path / "agent.json").write_text("{ not json", encoding="utf-8")
    assert hotkey.read_hotkey(tmp_path) == "alt+f8"                      # a broken file never disables it by accident


def test_off_and_invalid_shortcuts_register_nothing_and_say_why():
    ok, msg = hotkey.register(0, 1, "off")
    assert not ok and "switched off" in msg
    ok, msg = hotkey.register(0, 1, "alt+banana")
    assert not ok and "not valid" in msg


# ------------------------------------------------------------------ talking to the server as this computer
def link_with(handler):
    return ServerLink("https://deskhand.example", "dev1", "tok123", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_link_identifies_itself_with_the_device_token_and_returns_the_answer():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, auth=request.headers["authorization"], dev=request.headers["x-device-id"],
                    body=json.loads(request.content or b"{}"))
        return httpx.Response(202, json={"task_id": "abc"})
    assert link_with(handler).submit("open notepad") == {"task_id": "abc"}
    assert seen == {"path": "/agent/tasks", "auth": "Bearer tok123", "dev": "dev1", "body": {"goal": "open notepad"}}


def test_server_refusals_become_plain_messages_and_an_unreachable_server_says_so():
    ln = link_with(lambda r: httpx.Response(429, json={"detail": "this workspace reached its daily task limit; try again tomorrow"}))
    with pytest.raises(LinkError, match="daily task limit"):
        ln.submit("x")

    def down(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(LinkError, match="Could not reach"):
        link_with(down).status("t")


def test_the_server_address_is_worked_out_from_the_helpers_settings():
    ln = ServerLink.from_config({"server_url": "wss://deskhand.fawwazkhan.dev/agent/ws", "device_id": "d"}, "t")
    assert ln.base == "https://deskhand.fawwazkhan.dev" and ln.device_id == "d"


# ------------------------------------------------------------------ the local prompt server
class FakeLink:
    def __init__(self):
        self.calls = []

    def submit(self, goal): self.calls.append(("submit", goal)); return {"task_id": "t1"}
    def status(self, tid): self.calls.append(("status", tid)); return {"status": "completed", "terminal": True, "result": "done", "goal": "g"}
    def cancel(self, tid): self.calls.append(("cancel", tid)); return {"cancelled": True}
    def approve(self, tid, ok): self.calls.append(("approve", tid, ok)); return {"approved": ok}
    def answer(self, tid, choice, text): self.calls.append(("answer", tid, choice, text)); return {"answered": True}


@pytest.fixture
def bridge(tmp_path):
    b = VoiceBridge(FakeLink(), tmp_path, secret="s3cret")
    b.start()
    yield b
    b.stop()


def hit(b, method, path, *, headers=None, **kw):
    h = {"Host": f"127.0.0.1:{b.port}", **(headers or {})}
    return httpx.request(method, f"http://127.0.0.1:{b.port}{path}", headers=h, **kw)


def authed(b, **extra):
    return {"X-Deskhand-Local": b.secret, "Origin": f"http://127.0.0.1:{b.port}", **extra}


def test_the_page_is_served_with_a_strict_policy_and_no_secret_inside_it(bridge):
    r = hit(bridge, "GET", "/voice")
    assert r.status_code == 200 and "Deskhand voice" in r.text and "s3cret" not in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
    assert hit(bridge, "GET", "/voice/voice.js").status_code == 200 and hit(bridge, "GET", "/voice/voice.css").status_code == 200
    assert hit(bridge, "GET", "/etc/passwd").status_code == 404


def test_nothing_can_be_done_without_the_secret_or_from_another_website(bridge):
    body = {"goal": "open notepad"}
    assert hit(bridge, "POST", "/voice/submit", json=body).status_code == 401                          # no secret
    assert hit(bridge, "POST", "/voice/submit", json=body, headers={"X-Deskhand-Local": "wrong"}).status_code == 401
    evil = authed(bridge, Origin="https://evil.example")
    assert hit(bridge, "POST", "/voice/submit", json=body, headers=evil).status_code == 401          # right secret, wrong website
    assert hit(bridge, "GET", "/voice/status?tid=t1").status_code == 401
    assert bridge.link.calls == []


def test_dns_rebinding_is_refused(bridge):
    r = httpx.get(f"http://127.0.0.1:{bridge.port}/voice", headers={"Host": "attacker.example"})
    assert r.status_code == 403


def test_a_command_is_forwarded_and_followed(bridge):
    r = hit(bridge, "POST", "/voice/submit", json={"goal": "  open notepad  "}, headers=authed(bridge))
    assert r.status_code == 200 and r.json() == {"task_id": "t1"}
    assert hit(bridge, "GET", "/voice/status?tid=t1", headers=authed(bridge)).json()["result"] == "done"
    hit(bridge, "POST", "/voice/approve", json={"tid": "t1", "approved": True}, headers=authed(bridge))
    hit(bridge, "POST", "/voice/answer", json={"tid": "t1", "choice": "p1"}, headers=authed(bridge))
    hit(bridge, "POST", "/voice/cancel", json={"tid": "t1"}, headers=authed(bridge))
    assert bridge.link.calls == [("submit", "open notepad"), ("status", "t1"), ("approve", "t1", True),
                                 ("answer", "t1", "p1", None), ("cancel", "t1")]


def test_empty_or_enormous_commands_are_refused_before_they_reach_the_server(bridge):
    for goal in ("", "   ", "x" * 1001):
        assert hit(bridge, "POST", "/voice/submit", json={"goal": goal}, headers=authed(bridge)).status_code == 422
    assert bridge.link.calls == []


def test_a_server_problem_reaches_the_prompt_as_a_plain_message(bridge):
    def boom(goal): raise LinkError("this workspace reached its daily task limit; try again tomorrow")
    bridge.link.submit = boom
    r = hit(bridge, "POST", "/voice/submit", json={"goal": "x"}, headers=authed(bridge))
    assert r.status_code == 502 and "daily task limit" in r.json()["error"]


def test_the_helper_tells_the_app_where_the_prompt_lives_and_forgets_when_it_stops(bridge, tmp_path):
    pub = voice.read_published(tmp_path)
    assert pub["port"] == bridge.port and pub["secret"] == "s3cret"
    bridge.stop()
    assert voice.read_published(tmp_path) is None                                                  # a stale file is never trusted


def test_a_stale_file_from_a_helper_that_is_gone_is_ignored(tmp_path):
    (tmp_path / "voice.json").write_text(json.dumps({"port": 1, "secret": "x", "pid": 1, "at": 0}), encoding="utf-8")
    assert voice.read_published(tmp_path) is None


def test_the_helper_starts_the_prompt_only_when_it_knows_who_it_is(monkeypatch, tmp_path):
    monkeypatch.setenv("PILOT_HOME", str(tmp_path))
    assert voice.start_for_helper({}, "tok") is None and voice.start_for_helper({"server_url": "wss://x/agent/ws", "device_id": "d"}, "") is None
    b = voice.start_for_helper({"server_url": "wss://x/agent/ws", "device_id": "d"}, "tok")
    try:
        assert b is not None and (tmp_path / "voice.json").exists()
    finally:
        b.stop()


# ------------------------------------------------------------------ opening it
def test_the_window_opens_low_and_central_with_the_secret_only_in_the_address_fragment():
    args = voice.overlay_args("chrome.exe", 5555, "SEC", Path("P"), screen=(1920, 1080), scale=1.0)
    assert args[1] == "--app=http://127.0.0.1:5555/voice#SEC"                                       # a fragment is never sent anywhere
    assert "--window-size=560,340" in args and "--window-position=680,650" in args
    assert "--use-fake-ui-for-media-stream" in args and any(a.startswith("--user-data-dir=") for a in args)
    scaled = voice.overlay_args("chrome.exe", 1, "S", Path("P"), screen=(3840, 2160), scale=2.0)
    assert "--window-position=680,650" in scaled                                                    # high-DPI screens land in the same spot


def test_the_page_is_self_contained_and_only_talks_to_the_helper_it_came_from():
    assert 'src="/voice/voice.js"' in voice_ui.HTML and 'href="/voice/voice.css"' in voice_ui.HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)", voice_ui.HTML) and not re.search(r"\son\w+\s*=", voice_ui.HTML)
    assert "http://" not in voice_ui.JS and "https://" not in voice_ui.JS and "eval(" not in voice_ui.JS
    assert "X-Deskhand-Local" in voice_ui.JS and "webkitSpeechRecognition" in voice_ui.JS and "/voice/submit" in voice_ui.JS
    assert "innerHTML" not in voice_ui.JS                                                           # what is said or answered is never run as markup


def test_the_page_asks_questions_with_buttons_like_the_app_does():
    for needle in ("choices", "askForm", "approveRow", "pending_approval", "Something else"):
        assert needle in voice_ui.JS or needle in voice_ui.HTML, needle


# ------------------------------------------------------------------ the server's side of it
def server(tmp_path):
    from fastapi.testclient import TestClient
    from taskpilot.devices import DeviceHub
    from taskpilot.model import ScriptedRuntime
    from taskpilot.orchestrator import Config, Orchestrator
    from taskpilot.policy import Policy
    from taskpilot.server import create_app
    from taskpilot.store import Store
    store, hub = Store(), DeviceHub()
    app = create_app(store, hub, Orchestrator(store, hub, ScriptedRuntime([]), Policy.from_dict({}), Config()), "adminkey")
    return app, store


def enroll(c, name, workspace="main"):
    r = c.post("/devices", json={"device_id": name, "name": name, "workspace": workspace}, headers={"X-API-Key": "adminkey"})
    return {"X-Device-Id": name, "Authorization": "Bearer " + r.json()["token"]}


def test_a_computer_can_start_follow_and_stop_a_task_for_itself(tmp_path):
    from fastapi.testclient import TestClient
    app, store = server(tmp_path)
    with TestClient(app) as c:
        h = enroll(c, "laptop1")
        r = c.post("/agent/tasks", json={"goal": "open notepad"}, headers=h)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        t = c.get(f"/agent/tasks/{tid}", headers=h).json()
        assert t["goal"] == "open notepad" and t["device_id"] == "laptop1"
        assert store.get_task(tid)["device_id"] == "laptop1"
        assert c.post(f"/agent/tasks/{tid}/approve", json={"approved": True}, headers=h).status_code == 409      # nothing waiting
        assert c.post(f"/agent/tasks/{tid}/answer", json={"choice": "x"}, headers=h).status_code == 409
        assert c.post(f"/agent/tasks/{tid}/cancel", headers=h).status_code == 200


def test_a_computer_cannot_use_a_wrong_token_or_touch_another_computers_tasks(tmp_path):
    from fastapi.testclient import TestClient
    app, _ = server(tmp_path)
    with TestClient(app) as c:
        a, b = enroll(c, "a"), enroll(c, "b")
        tid = c.post("/agent/tasks", json={"goal": "private thing"}, headers=a).json()["task_id"]
        assert c.post("/agent/tasks", json={"goal": "x"}, headers={**a, "Authorization": "Bearer wrong"}).status_code == 401
        assert c.post("/agent/tasks", json={"goal": "x"}).status_code == 401
        assert c.post("/agent/tasks", json={"goal": "x"}, headers={"X-Device-Id": "a", "Authorization": b["Authorization"]}).status_code == 401
        assert c.get(f"/agent/tasks/{tid}", headers=b).status_code == 404                        # looks like it does not exist
        assert c.post(f"/agent/tasks/{tid}/cancel", headers=b).status_code == 404


def test_the_shortcut_counts_against_the_workspace_limit_and_cannot_be_spammed(tmp_path):
    from fastapi.testclient import TestClient
    app, store = server(tmp_path)
    with TestClient(app) as c:
        h = enroll(c, "a")
        store.update_workspace("main", max_tasks_per_day=2)
        assert c.post("/agent/tasks", json={"goal": "one"}, headers=h).status_code == 202
        assert c.post("/agent/tasks", json={"goal": "two"}, headers=h).status_code == 202
        r = c.post("/agent/tasks", json={"goal": "three"}, headers=h)
        assert r.status_code == 429 and "daily task limit" in r.json()["detail"]
        store.update_workspace("main", max_tasks_per_day=1000)
        codes = [c.post("/agent/tasks", json={"goal": f"g{i}"}, headers=h).status_code for i in range(14)]
        assert 429 in codes and codes.count(202) <= 10                                           # a short burst is stopped
        store.update_workspace("main", disabled=True)
        assert c.post("/agent/tasks", json={"goal": "x"}, headers=h).status_code in (403, 429)


# ------------------------------------------------------------------ changing the shortcut
@pytest.mark.parametrize("text", ["alt+f8", "ctrl+alt+v", "ctrl+shift+space", "alt+f24", "ctrl+1", "shift+alt+f"])
def test_what_the_box_shows_and_what_is_saved_agree(text):
    v = hotkey.to_control(text)
    again = hotkey.from_control(v)
    assert hotkey.parse_hotkey(again) == hotkey.parse_hotkey(text)


def test_keys_the_box_cannot_turn_into_a_shortcut_are_explained():
    with pytest.raises(hotkey.BadHotkey, match="press the keys"):
        hotkey.from_control(0)
    with pytest.raises(hotkey.BadHotkey, match="Ctrl, Alt or Shift|modifier"):
        hotkey.from_control(ord("K"))                                    # a letter alone would break typing everywhere
    with pytest.raises(hotkey.BadHotkey):
        hotkey.from_control((hotkey.HOTKEYF_ALT << 8) | 0xE7)           # not a key we can name
    with pytest.raises(hotkey.BadHotkey, match="Windows key"):
        hotkey.to_control("win+space")


def test_saving_a_new_shortcut_keeps_the_rest_of_the_settings_file(tmp_path):
    (tmp_path / "agent.json").write_text("﻿" + json.dumps({"server_url": "wss://x/agent/ws", "device_id": "d",
                                                                 "policy": {"cloud_safe": True}}), encoding="utf-8")
    hotkey.write_hotkey(tmp_path, "ctrl+alt+v")
    saved = json.loads((tmp_path / "agent.json").read_text(encoding="utf-8"))               # readable without the byte-order mark
    assert saved == {"server_url": "wss://x/agent/ws", "device_id": "d", "policy": {"cloud_safe": True}, "hotkey": "ctrl+alt+v"}
    assert hotkey.read_hotkey(tmp_path) == "ctrl+alt+v" and not list(tmp_path.glob("*.tmp"))
    hotkey.write_hotkey(tmp_path, "off")
    assert hotkey.read_hotkey(tmp_path) == "off"                                            # off is remembered, not replaced by the default


def test_saving_creates_the_file_if_there_is_none(tmp_path):
    hotkey.write_hotkey(tmp_path, "alt+f9")
    assert json.loads((tmp_path / "agent.json").read_text(encoding="utf-8")) == {"hotkey": "alt+f9"}
