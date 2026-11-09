import os

import pytest

from taskpilot.agent import uia
from taskpilot.playbooks import playbooks_for
from taskpilot.protocol import ActionAdapter, ActionRequest, CaptureScreen, GetActiveWindow
from taskpilot.tools.ui_tools import SnapshotArgs

from conftest import act, done, make_policy


def req(action):
    return ActionRequest(action_id=os.urandom(4).hex(), task_id="t", action=action)


def call(tool_name, /, **args):
    return ActionAdapter.validate_python({"tool": "app_tool", "name": tool_name, "args": args})


# ------------------------------------------------------------------ screenshots: approved window only

async def test_privacy_mode_captures_only_the_approved_foreground_window(make_env):
    e = make_env([])
    e.policy.cloud_safe = True
    e.backend.active = "Untitled - Notepad"                        # approved (app id in the title)
    r = await e.executor.handle(req(CaptureScreen()))
    assert r.ok and r.screenshot.image_b64 == "WIN" and e.backend.calls == [("window_shot",)]


async def test_privacy_mode_refuses_to_capture_any_other_window(make_env):
    e = make_env([])
    e.policy.cloud_safe = True
    e.backend.windows = []                                           # no approved window exists to bring forward
    for title in ("Discord | #general", "Gmail - Inbox", "Command Prompt - cmd", "Desktop"):
        e.backend.active = title
        r = await e.executor.handle(req(CaptureScreen()))
        assert r.status == "denied" and r.screenshot is None and "privacy mode" in r.error, title
    assert e.backend.calls == []                                    # no pixels were even read


async def test_normal_mode_still_captures_the_whole_display(make_env):
    e = make_env([])
    e.backend.active = "Gmail - Inbox"
    r = await e.executor.handle(req(CaptureScreen()))
    assert r.ok and r.screenshot.image_b64 == "AAAA" and ("window_shot",) not in e.backend.calls


async def test_privacy_mode_hides_unapproved_window_titles(make_env):
    e = make_env([])
    e.policy.cloud_safe = True
    e.backend.active = "WhatsApp Web chat with Mum"
    assert (await e.executor.handle(req(GetActiveWindow()))).data["window"] is None
    e.backend.active = "Untitled - Notepad"
    assert (await e.executor.handle(req(GetActiveWindow()))).data["window"]["title"] == "Untitled - Notepad"


async def test_window_list_and_focus_are_filtered_in_privacy_mode(make_env):
    e = make_env([])
    e.backend.windows += [{"title": "Inbox (3) - me@example.com - Gmail", "process": "chrome.exe", "pid": 5},
                          {"title": "Chat with Mum - WhatsApp", "process": "chrome.exe", "pid": 6}]
    open_list = await e.executor.handle(req(call("windows.list")))
    assert len(open_list.data["windows"]) == 3                      # normal mode: everything (legacy behaviour)
    e.policy.cloud_safe = True
    safe = await e.executor.handle(req(call("windows.list")))
    assert [w["title"] for w in safe.data["windows"]] == ["Untitled - Notepad"]
    assert "Gmail" not in str(safe.data) and "Mum" not in str(safe.data)
    f = await e.executor.handle(req(call("windows.focus", title="Gmail")))
    assert f.status == "denied" and f.data["needs_access"]["kind"] in ("site", "window")     # never focused: it ASKS
    assert "me@example.com" not in f.error and "Inbox" not in f.error          # the private title is never echoed
    assert not any(c[0] == "focus" and "Gmail" in c[1] for c in e.backend.calls)
    assert (await e.executor.handle(req(call("windows.focus", title="Notepad")))).ok


async def test_refused_capture_is_feedback_to_the_model_not_a_crash(make_env):
    e = make_env([act("capture_screen"), {"outcome": {"outcome": "cannot_proceed", "message": "nothing approved is open"}}],
                 screenshot_mode="auto")
    e.policy.cloud_safe = True
    e.backend.windows = []
    e.backend.active = "Discord | #general"                          # the user is in another app
    tid, t = await e.run("look at the page")
    c = e.model.contexts
    assert c[1].screenshot_b64 is None
    note = " ".join(c[1].notes)
    assert "privacy mode" in note and "windows.focus" in note and "Do NOT open a page you already opened" in note
    assert ("window_shot",) not in e.backend.calls


async def test_an_approved_window_is_brought_forward_instead_of_refusing(make_env):
    """The page was opened but another app is in front: Pilot focuses the approved window and captures only that."""
    e = make_env([])
    e.policy.cloud_safe = True
    e.backend.active = "Discord | #general"
    r = await e.executor.handle(req(CaptureScreen()))
    assert r.ok and r.screenshot.image_b64 == "WIN"
    assert ("focus", "Untitled - Notepad") in e.backend.calls and e.backend.active == "Untitled - Notepad"
    assert not any(c[0] == "click" for c in e.backend.calls)


async def test_the_same_page_is_never_opened_twice_in_a_task(make_env):
    url = "https://www.youtube.com/@BattleBots/videos"
    e = make_env([act("browser.open_url", url=url), act("browser.open_url", url=url + "/"),
                  act("browser.open_url", url=url.upper()), act("browser.open_url", url=url),
                  act("browser.open_url", url=url), act("browser.open_url", url=url),
                  {"outcome": {"outcome": "cannot_proceed", "message": "x"}}],
                 screenshot_mode="auto")
    tid, t = await e.run("g")
    assert [c for c in e.backend.calls if c[0] == "url"] == [("url", url), ("url", url + "/")]   # the page + ONE retry, never more
    assert "kept re-opening the same page" in t["error"]
    assert any("already tried opening this page twice" in n for n in e.model.contexts[-1].notes)


async def test_clicks_on_a_cropped_window_screenshot_map_to_real_display_positions(make_env):
    e = make_env([act("capture_screen"), act("click", x=10, y=20), done("clicked")], screenshot_mode="auto")
    e.policy.cloud_safe = True
    e.backend.active = "Untitled - Notepad"
    tid, t = await e.run("click")
    assert t["status"] == "completed", t
    # crop origin (100, 50), 2 display px per image px: (10, 20) -> (100 + 20, 50 + 40)
    assert [c for c in e.backend.calls if c[0] == "click"] == [("click", 120, 90, "left", 1)]


# ------------------------------------------------------------------ private browser window

async def test_open_private_is_disabled_unless_the_policy_allows_it(make_env, tmp_path):
    e = make_env([])
    r = await e.executor.handle(req(call("browser.open_private", url="https://www.youtube.com/@BattleBots/videos")))
    assert r.status == "denied" and "disabled by policy" in r.error and e.backend.calls == []
    assert (await e.executor.handle(req(call("browser.open_url", url="https://www.youtube.com/@BattleBots/videos")))).ok


async def test_open_private_opens_a_private_window_and_obeys_the_url_policy(make_env, tmp_path):
    e = make_env([])
    e.policy.allow_private_windows = True
    r = await e.executor.handle(req(call("browser.open_private", url="https://www.youtube.com/@BattleBots/videos")))
    assert r.ok and r.data["private"] is True and e.backend.calls == [("private_url", "https://www.youtube.com/@BattleBots/videos")]
    p = make_policy(tmp_path)
    p.allow_private_windows = True
    for bad in ("file:///C:/Windows/win.ini", "http://localhost:8765/", "https://user:pw@example.com/"):
        assert p.evaluate(call("browser.open_private", url=bad)).verdict == "deny", bad
    p.allowed_domains = ["youtube.com"]
    assert p.evaluate(call("browser.open_private", url="https://news.google.com/")).verdict == "deny"


# ------------------------------------------------------------------ long text + playbooks

def test_snapshot_names_can_be_lengthened_for_reading_prose(tmp_path):
    el = {"role": "Hyperlink", "name": "H" * 300, "id": "", "enabled": True, "ctrl": None}
    assert len(uia._describe(el, None, 90)) < 120
    assert len(uia._describe(el, None, 250)) > 250
    p = make_policy(tmp_path)
    assert p.evaluate(call("ui.snapshot", window="x", name_chars=400)).allowed
    assert p.evaluate(call("ui.snapshot", window="x", name_chars=5000)).verdict == "deny"
    assert SnapshotArgs().name_chars == 90


def test_playbooks_for_the_three_tasks():
    yt = playbooks_for("Open youtube and play the latest video from battle bots")
    assert "browser.open_url" in yt and "never a private window" in yt and "NEWEST FIRST" in yt
    assert "browser.open_private" not in yt and "SUCCESS" in yt
    news = playbooks_for("Research on what is the new AI news today")
    assert "news.google.com" in news and "name_chars=200" in news and "Do NOT click" in news
    gpt = playbooks_for("Open chatgpt and ask what is the best cheapest 25 kg Servo motor i can find")
    assert "chatgpt.com" in gpt and "CAPTCHA" in gpt and "never try to bypass" in gpt
    assert "[YouTube]" not in gpt and "[News research]" not in gpt


def test_the_model_can_say_link_or_textbox():
    els = [{"role": "Hyperlink", "name": "Watch now", "id": "", "enabled": True},
           {"role": "Edit", "name": "Search", "id": "q", "enabled": True},
           {"role": "TabItem", "name": "Videos", "id": "", "enabled": True}]
    assert [e["role"] for e in uia._matches(els, {"name": "Watch", "role": "Link"})] == ["Hyperlink"]
    assert [e["role"] for e in uia._matches(els, {"name": "Search", "role": "textbox"})] == ["Edit"]
    assert [e["role"] for e in uia._matches(els, {"name": "Videos", "role": "tab"})] == ["TabItem"]


async def test_a_confused_model_cannot_spam_browser_windows(make_env):
    script = [act("browser.open_url", url=f"https://example.com/page{i}") for i in range(10)]
    e = make_env(script, screenshot_mode="auto")
    tid, t = await e.run("g")
    assert t["status"] == "failed" and "more than 5 different pages" in t["error"]
    assert len([c for c in e.backend.calls if c[0] == "url"]) == 5


def test_names_copied_from_a_shortened_snapshot_line_still_match():
    real = "All The Bot Fights From The Entire Season | BATTLEBOTS PRO LEAGUE POWERED BY BRIGHT DATA 1 hour, 48 minutes"
    els = [{"role": "Hyperlink", "name": real, "id": "", "enabled": True},
           {"role": "Text", "name": "Something else", "id": "", "enabled": True}]
    shown = uia._short(real, 60)                                   # what ui.snapshot prints: ends with an ellipsis
    assert shown.endswith("…")
    for typed in (shown, shown[:-1] + "...", "all the bot  fights from the entire season", real):
        assert [e["role"] for e in uia._matches(els, {"name": typed})] == ["Hyperlink"], typed
    assert uia._matches(els, {"name": "A different title…"}) == []


class _Node:
    """Minimal stand-in for a UIA control, to test walk ORDER without a real desktop."""
    def __init__(self, name, kids=(), role="HyperlinkControl", off=False):
        self.Name, self._kids, self.ControlTypeName, self.IsOffscreen = name, list(kids), role, off
        self.AutomationId, self.IsEnabled = "", True

    def GetChildren(self):
        return self._kids


def test_walk_returns_controls_in_true_document_order():
    """Regression: siblings used to come back reversed, so 'the first video' was the LAST one on the page."""
    grid = _Node("grid", [
        _Node("video 1 (newest)", [_Node("video 1 title"), _Node("video 1 menu")]),
        _Node("video 2", [_Node("video 2 title")]),
        _Node("video 3", [_Node("video 3 title")]),
    ], role="GroupControl")
    root = _Node("root", [_Node("toolbar", [_Node("back"), _Node("forward")]), grid], role="WindowControl")
    names = [e["name"] for e in uia._walk(root, True)]
    assert names == ["toolbar", "back", "forward", "grid", "video 1 (newest)", "video 1 title", "video 1 menu",
                     "video 2", "video 2 title", "video 3", "video 3 title"]
    # and offscreen controls are skipped unless asked for, without losing their visible children's order
    root2 = _Node("root", [_Node("a"), _Node("hidden", [_Node("b")], off=True), _Node("c")], role="WindowControl")
    assert [e["name"] for e in uia._walk(root2, False)] == ["a", "b", "c"]
    assert [e["name"] for e in uia._walk(root2, True)] == ["a", "hidden", "b", "c"]


# ------------------------------------------------------------------ approving a browser window by the SITE it shows

def _chat_policy(e):
    e.policy.approved_apps.clear()
    e.policy.ui_allowed_windows = []
    e.policy.ui_allowed_hosts = ["chatgpt.com", "claude.ai"]
    e.policy.cloud_safe = True
    e.backend.windows = [{"title": "Find Cheapest Servo Motor - Google Chrome", "process": "chrome.exe", "pid": 7},
                         {"title": "Inbox (3) - Gmail - Google Chrome", "process": "chrome.exe", "pid": 8}]
    e.backend.hosts = {"Find Cheapest Servo Motor - Google Chrome": "chatgpt.com",
                       "Inbox (3) - Gmail - Google Chrome": "mail.google.com"}


async def test_a_chat_window_is_approved_by_its_site_even_when_the_title_is_the_chat_name(make_env):
    e = make_env([])
    _chat_policy(e)
    e.backend.active = "Find Cheapest Servo Motor - Google Chrome"           # no 'ChatGPT' anywhere in the title
    e.backend.active_process = "chrome.exe"
    r = await e.executor.handle(req(CaptureScreen()))
    assert r.ok and r.screenshot.image_b64 == "WIN"


async def test_other_sites_in_the_same_browser_stay_refused_hidden_and_unfocusable(make_env):
    e = make_env([])
    _chat_policy(e)
    e.backend.active = "Inbox (3) - Gmail - Google Chrome"
    e.backend.active_process = "chrome.exe"
    assert (await e.executor.handle(req(GetActiveWindow()))).data["window"] is None
    listed = await e.executor.handle(req(call("windows.list")))
    assert [w["title"] for w in listed.data["windows"]] == ["Find Cheapest Servo Motor - Google Chrome"]
    assert "Gmail" not in str(listed.data)
    refused = await e.executor.handle(req(call("windows.focus", title="Gmail")))
    assert refused.status == "denied" and refused.data["needs_access"]["target"] == "mail.google.com"   # exact host, never "google.com"
    assert not any(c[0] == "focus" and "Gmail" in c[1] for c in e.backend.calls)
    # the capture refuses the Gmail window and brings the chat forward instead of ever reading Gmail
    r = await e.executor.handle(req(CaptureScreen()))
    assert r.ok and e.backend.active == "Find Cheapest Servo Motor - Google Chrome"


async def test_a_host_rule_never_overrides_a_protected_window(make_env):
    e = make_env([])
    _chat_policy(e)
    e.policy.ui_deny_titles = ["servo"]                                      # explicitly protected
    e.backend.active = "Find Cheapest Servo Motor - Google Chrome"
    e.backend.active_process = "chrome.exe"
    r = await e.executor.handle(req(CaptureScreen()))
    assert r.status == "denied" and "protected" in r.error


async def test_host_rule_only_applies_to_browsers_and_only_to_listed_hosts(make_env):
    e = make_env([])
    _chat_policy(e)
    e.backend.windows += [{"title": "Notes", "process": "notes.exe", "pid": 9}]
    e.backend.hosts["Notes"] = "chatgpt.com"                                 # not a browser: host is ignored
    from taskpilot.agent.gate import window_allowed
    assert not (await window_allowed(e.policy, e.backend, "Notes", "notes.exe")).allowed
    assert not (await window_allowed(e.policy, e.backend, "Evil - Google Chrome", "chrome.exe")).allowed
    e.backend.hosts["Evil - Google Chrome"] = "chatgpt.com.evil.example"
    assert not (await window_allowed(e.policy, e.backend, "Evil - Google Chrome", "chrome.exe")).allowed
    assert (await window_allowed(e.policy, e.backend, "Find Cheapest Servo Motor - Google Chrome", "chrome.exe")).allowed


def test_policy_host_matching_includes_subdomains_but_not_lookalikes(tmp_path):
    p = make_policy(tmp_path)
    p.ui_allowed_hosts = ["chatgpt.com"]
    assert p.ui_window_allowed("x", "chrome.exe", "chatgpt.com").allowed
    assert p.ui_window_allowed("x", "chrome.exe", "chat.chatgpt.com").allowed
    assert not p.ui_window_allowed("x", "chrome.exe", "notchatgpt.com").allowed
    assert not p.ui_window_allowed("x", "chrome.exe", "chatgpt.com.evil.io").allowed
    assert not p.ui_window_allowed("x", "chrome.exe", None).allowed


def test_typing_never_sends_a_newline_and_braces_stay_literal():
    assert uia._escape_sendkeys("line one\nline two") == "line one line two"
    assert uia._escape_sendkeys("a{Enter}b") == "a{{}Enter{}}b"


def test_claude_and_chat_playbooks():
    claude = playbooks_for("Open claude and ask what is the best cheapest 25 kg Servo motor i can find")
    assert "claude.ai/new" in claude and "window=''" in claude
    assert "[ChatGPT]" not in claude
    gpt = playbooks_for("Open chatgpt and ask something")
    assert "window=''" in gpt and "[Claude]" not in gpt
