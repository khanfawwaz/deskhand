"""Exploring pages: listing links, paging through long lists, waiting for a page to load, and nudges (not rules)."""
import pytest

from taskpilot.agent import uia
from taskpilot.playbooks import playbooks_for
from taskpilot.protocol import ActionRequest, AppToolCall
from taskpilot.tools import get_registry
from taskpilot.tools.builtin import _wait_for_page

from conftest import act, done


def call(name, **args):
    return AppToolCall(name=name, args=args)


def req(action, n=[0]):
    n[0] += 1
    return ActionRequest(action_id=f"x{n[0]}", task_id="t", action=action)


# ------------------------------------------------------------------ ui.snapshot: filter to links, page further down
async def test_snapshot_can_list_only_links_and_page_through_them(make_env):
    e = make_env([])
    e.backend.ui.windows["Contact form"] = {"process": "formapp.exe", "pid": 101}
    r = await e.executor.handle(req(call("ui.snapshot", window="Contact form", role="Hyperlink")))
    assert r.ok and r.data["count"] == 1 and "Privacy policy" in r.data["tree"] and "Button" not in r.data["tree"]
    r = await e.executor.handle(req(call("ui.snapshot", window="Contact form", role="link")))       # "link" means Hyperlink too
    assert r.data["count"] == 1
    allc = await e.executor.handle(req(call("ui.snapshot", window="Contact form", max_elements=5)))
    page2 = await e.executor.handle(req(call("ui.snapshot", window="Contact form", max_elements=5, skip=5)))
    assert allc.data["tree"] != page2.data["tree"] and page2.ok                                     # skip moves further down


def test_role_matching_understands_the_names_people_use():
    assert uia._role_matches("link", "Hyperlink") and uia._role_matches("Hyperlink", "Hyperlink")
    assert uia._role_matches("textbox", "Edit") and not uia._role_matches("button", "Hyperlink")


def test_the_snapshot_schema_accepts_role_and_skip_with_sane_limits():
    p = get_registry().get("ui.snapshot").params
    assert p.model_validate({"role": "Hyperlink", "skip": 50}).skip == 50
    with pytest.raises(Exception):
        p.model_validate({"skip": -1})


# ------------------------------------------------------------------ opening a page waits until its window is there
async def test_opening_a_page_waits_for_its_window_and_reports_it(make_env):
    e = make_env([])
    e.policy.page_wait_s = 1.0
    e.policy.cloud_safe = True
    e.policy.ui_allowed_hosts = ["youtube.com"]
    title = "(3) SSundeePlus - YouTube - Google Chrome"
    e.backend.active, e.backend.active_process = title, "chrome.exe"
    e.backend.urls[title] = "https://www.youtube.com/@SSundeePlus/videos"                 # case differs from what was asked
    from taskpilot.tools.registry import ToolContext
    ctx = ToolContext(e.backend, e.policy, e.executor.managed, "t")
    assert await _wait_for_page(ctx, "https://www.youtube.com/@ssundeeplus/videos") == (title, e.backend.urls[title])
    e.policy.page_wait_s = 0.3
    assert await _wait_for_page(ctx, "https://www.youtube.com/@someone-else/videos") == ("", "")   # a LEFT-OVER window is not the page


def test_same_page_comparison_is_forgiving_about_case_www_and_query_but_not_about_videos():
    from taskpilot.tools.builtin import _same_page
    assert _same_page("youtube.com/@SSundeePlus/videos", "https://www.youtube.com/@ssundeeplus/videos")
    assert _same_page("https://www.youtube.com/@a/videos?x=1", "https://www.youtube.com/@a/videos")
    assert _same_page("https://www.youtube.com/watch?v=abc&t=5", "https://www.youtube.com/watch?v=abc")
    assert not _same_page("https://www.youtube.com/watch?v=OLD", "https://www.youtube.com/watch?v=NEW")
    assert not _same_page("https://www.youtube.com/@a/videos", "https://www.example.com/@a/videos")


async def test_waiting_gives_up_quietly_and_never_leaks_an_unapproved_title(make_env):
    from taskpilot.tools.registry import ToolContext
    e = make_env([])
    e.policy.page_wait_s = 0.3
    e.policy.cloud_safe = True                                                           # privacy mode: titles of unapproved windows stay hidden
    e.backend.active, e.backend.active_process = "Inbox - me@example.com - Gmail", "chrome.exe"
    e.backend.hosts["Inbox - me@example.com - Gmail"] = "mail.google.com"
    ctx = ToolContext(e.backend, e.policy, e.executor.managed, "t")
    assert await _wait_for_page(ctx, "https://mail.google.com/") == ("", "")            # not approved: no title is returned
    e.backend.active, e.backend.active_process = "Notepad", "notepad.exe"
    assert await _wait_for_page(ctx, "https://www.youtube.com/") == ("", "")            # not a browser: gave up, no error


# ------------------------------------------------------------------ nudges, not rules
async def test_a_long_look_around_gets_a_nudge_toward_other_ways_to_see_and_the_task_keeps_going(make_env):
    looks = [act("ui.snapshot", window="Calculator", query=f"q{i}") for i in range(7)]
    e = make_env(looks + [act("ui.click", window="Calculator", name="Seven"), done("Calculator display now shows Display is 7")],
                 screenshot_mode="auto")
    tid, t = await e.run("g", max_actions=30)
    assert t["status"] == "completed", t                                                  # it recovered: no early abort
    nudge = [n for c in e.model.contexts for n in c.notes if "only looked around for 4 steps" in n]
    assert nudge and "capture_screen" in nudge[0] and "role=Hyperlink" in nudge[0] and "min_chars=25" in nudge[0] \
        and "click it now" in nudge[0]


async def test_there_is_more_room_before_giving_up_but_still_a_limit(make_env):
    looks = [act("ui.snapshot", window="Calculator", query=f"q{i}") for i in range(30)]
    e = make_env(looks, screenshot_mode="auto")
    tid, t = await e.run("g", max_actions=40)
    assert t["status"] == "failed" and "no progress" in t["error"] and "16 steps" in t["error"]


def test_the_youtube_playbook_tells_it_how_to_see_the_videos_without_forbidding_anything():
    text = playbooks_for("play the latest video from Ssundeeplus on youtube")
    assert "role=Hyperlink" in text and "min_chars=25" in text and "newest" in text and "capture_screen" in text


# ------------------------------------------------------------------ "done" is checked against the browser's real address
def test_which_goals_must_end_on_a_video_page():
    from taskpilot.playbooks import final_address_for
    assert "/watch" in final_address_for("Open youtube and play the latest video from Ssundeeplus")
    assert "/watch" in final_address_for("watch the NetworkChuck paperclip video on YouTube")
    assert final_address_for("Open YouTube") == () and final_address_for("play some music on spotify") == ()
    assert final_address_for("what is the news today") == ()


def _youtube_env(make_env, address, script):
    e = make_env(script, screenshot_mode="auto")
    title = "SSundeePlus - YouTube - Google Chrome"
    e.policy.cloud_safe = True
    e.policy.ui_allowed_hosts = ["youtube.com"]
    e.backend.windows.append({"title": title, "process": "chrome.exe", "pid": 9})
    e.backend.active, e.backend.active_process = title, "chrome.exe"
    e.backend.urls[title] = address
    return e


async def test_done_is_refused_while_the_browser_is_still_on_the_channel_list(make_env):
    """The exact false success from real use: it said 'playing' but was still looking at the channel's video list."""
    url = "https://www.youtube.com/@SSundeePlus/videos"
    claim = done("Opened: the video is open and playing")
    e = _youtube_env(make_env, url, [act("browser.open_url", url=url), claim, claim, claim, claim, claim])
    tid, t = await e.run("Open youtube and play the latest video from Ssundeeplus")
    assert t["status"] == "failed"
    notes = " ".join(n for c in e.model.contexts for n in c.notes)
    assert "address bar shows" in notes and "/@SSundeePlus/videos" in notes and "not a video page" in notes


async def test_done_is_accepted_once_the_address_is_a_video(make_env):
    url = "https://www.youtube.com/@SSundeePlus/videos"
    e = _youtube_env(make_env, "https://www.youtube.com/watch?v=abc123", [act("browser.open_url", url=url),
                                                                         done("Opened: the video is open and playing")])
    tid, t = await e.run("Open youtube and play the latest video from Ssundeeplus")
    assert t["status"] == "completed", t


async def test_other_goals_are_not_address_checked(make_env):
    e = make_env([act("ui.click", window="Calculator", name="Seven"), done("Calculator display now shows Display is 7")],
                 screenshot_mode="auto")
    tid, t = await e.run("Press seven in calculator")
    assert t["status"] == "completed"


async def test_the_current_url_tool_only_answers_for_an_approved_browser_window(make_env):
    e = _youtube_env(make_env, "https://www.youtube.com/watch?v=abc", [])
    r = await e.executor.handle(req(call("browser.current_url")))
    assert r.ok and r.data["url"].endswith("watch?v=abc")
    e.backend.urls["SSundeePlus - YouTube - Google Chrome"] = "https://www.youtube.com/watch?v=abc"
    e.policy.ui_allowed_hosts = []                                                      # no longer approved
    r2 = await e.executor.handle(req(call("browser.current_url")))
    assert not r2.ok and "not approved" in r2.error
    e.backend.active, e.backend.active_process = "Notepad", "notepad.exe"
    assert "not a browser" in (await e.executor.handle(req(call("browser.current_url")))).error


async def test_snapshot_min_chars_keeps_only_long_content_names(make_env):
    e = make_env([])
    e.backend.ui.windows["Contact form"] = {"process": "formapp.exe", "pid": 101}
    allc = await e.executor.handle(req(call("ui.snapshot", window="Contact form")))
    longc = await e.executor.handle(req(call("ui.snapshot", window="Contact form", min_chars=15)))
    assert longc.ok and longc.data["count"] < allc.data["count"]
    assert get_registry().get("ui.snapshot").params.model_validate({"min_chars": 25}).min_chars == 25


def test_a_tab_titled_with_its_address_is_still_loading():
    from taskpilot.tools.builtin import _still_loading
    assert _still_loading("youtube.com/@NetworkChuck/videos - Google Chrome")
    assert _still_loading("youtube.com/results?search_query=NetworkChuck+paperclip - Google Chrome")
    assert not _still_loading("NetworkChuck - YouTube - Google Chrome")
    assert not _still_loading("(3) SSundeePlus - YouTube - Google Chrome")
