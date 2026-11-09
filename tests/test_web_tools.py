"""web.* tools: the browser bridge gives real links and the real address; site policy still applies."""
from taskpilot.protocol import ActionRequest, AppToolCall
from taskpilot.tools import get_registry

from conftest import act, done

CHANNEL = "https://www.youtube.com/@Chan/videos"
VIDEO = "https://www.youtube.com/watch?v=abc123"


def call(name, **args):
    return AppToolCall(name=name, args=args)


def req(action, n=[0]):
    n[0] += 1
    return ActionRequest(action_id=f"w{n[0]}", task_id="t", action=action)


def world(e):
    e.backend.webpage.pages[CHANNEL] = {"title": "Chan - YouTube", "links": [
        ("Home", "https://www.youtube.com/"), ("Newest video title here 12 minutes", VIDEO),
        ("Older video title there 30 minutes", "https://www.youtube.com/watch?v=old")]}
    e.backend.webpage.pages[VIDEO] = {"title": "Newest video title here - YouTube", "links": []}


def test_the_web_tools_are_registered_with_sane_limits():
    reg = get_registry()
    for n in ("web.open", "web.links", "web.click", "web.search", "web.url", "web.read"):
        assert reg.get(n) is not None, n
    assert reg.get("web.links").params.model_validate({"min_chars": 25}).min_chars == 25


async def test_open_list_click_reports_the_real_address(make_env):
    e = make_env([])
    world(e)
    r = await e.executor.handle(req(call("web.open", url=CHANNEL)))
    assert r.ok and r.data["loaded"] and r.data["address"] == CHANNEL
    r = await e.executor.handle(req(call("web.links", min_chars=20)))
    assert r.ok and r.data["count"] == 2 and "Newest video title here 12 minutes" in r.data["tree"]
    assert "Home" not in r.data["tree"]                                    # short menu names are filtered out
    r = await e.executor.handle(req(call("web.click", text="Newest video")))
    assert r.ok and r.data["address"] == VIDEO
    r = await e.executor.handle(req(call("web.url")))
    assert r.ok and r.data["address"] == VIDEO


async def test_search_uses_the_pages_search_box(make_env):
    e = make_env([])
    e.backend.webpage.address = "https://www.youtube.com/"
    r = await e.executor.handle(req(call("web.search", query="NetworkChuck paperclip")))
    assert r.ok and "search_query=NetworkChuck+paperclip" in r.data["address"]


async def test_private_and_internal_addresses_are_refused(make_env):
    e = make_env([])
    for url in ("http://localhost:8080/admin", "http://192.168.1.1/", "file:///C:/secret.txt", "http://169.254.169.254/latest"):
        r = await e.executor.handle(req(call("web.open", url=url)))
        assert not r.ok, url
    assert e.backend.webpage.log == []                                      # nothing was opened


async def test_unapproved_sites_need_approval_in_privacy_mode(make_env):
    e = make_env([])
    world(e)
    e.policy.require_site_approval = True
    e.policy.ui_allowed_hosts = []
    r = await e.executor.handle(req(call("web.open", url=CHANNEL)))
    assert not r.ok and r.status == "denied"
    e.policy.ui_allowed_hosts = ["youtube.com"]
    assert (await e.executor.handle(req(call("web.open", url=CHANNEL)))).ok


async def test_a_redirect_to_a_blocked_site_stops_reading_the_page(make_env):
    e = make_env([])
    world(e)
    e.policy.deny_hosts = ["accounts.google.com"]
    e.backend.webpage.address = "https://accounts.google.com/signin"
    r = await e.executor.handle(req(call("web.links")))
    assert not r.ok and "sign-in or payment" in (r.error or "")


async def test_done_for_a_youtube_goal_needs_a_video_address_even_with_the_bridge(make_env):
    """The final-address check reads the browser's real address through the bridge too."""
    e = make_env([act("web.open", url=CHANNEL), done("Opened: the video is open and playing")] + [done("playing")] * 4)
    world(e)
    e.policy.ui_allowed_hosts = ["youtube.com"]
    tid, t = await e.run("Open youtube and play the latest video from Chan")
    assert t["status"] == "failed"
    notes = " ".join(n for c in e.model.contexts for n in c.notes)
    assert "address" in notes and "not a video page" in notes


async def test_done_is_accepted_after_clicking_through_the_bridge(make_env):
    e = make_env([act("web.open", url=CHANNEL), act("web.click", text="Newest video"),
                  done("Opened: the video is open and playing")])
    world(e)
    e.policy.ui_allowed_hosts = ["youtube.com"]
    tid, t = await e.run("Open youtube and play the latest video from Chan")
    assert t["status"] == "completed", t
