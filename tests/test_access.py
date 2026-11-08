"""Access requests and questions: when Deskhand lacks permission it ASKS the user instead of dead-ending, and the
user's answer (not the model) is the only thing that widens what it may touch."""
import pytest

from taskpilot.policy import Policy, base_domain
from taskpilot.protocol import ActionRequest, GrantAccess

from conftest import act, done


def ask(question, *options):
    return {"outcome": {"outcome": "need_more_information", "message": question, "options": list(options)}}


# ------------------------------------------------------------------ policy building blocks
def test_base_domain_is_what_a_person_calls_the_site():
    assert base_domain("music.youtube.com") == "youtube.com"
    assert base_domain("www.openrouter.ai") == "openrouter.ai"
    assert base_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert base_domain("localhost") == "localhost"
    assert base_domain("www.google.com") == "www.google.com" and base_domain("mail.google.com") == "mail.google.com"


def test_approving_google_search_does_not_approve_gmail():
    p = Policy.from_dict({})
    p.grant("site", "www.google.com")
    assert p.site_allowed("www.google.com") and not p.site_allowed("mail.google.com") and not p.site_allowed("drive.google.com")
    assert not p.ui_window_allowed("Inbox - Gmail", "chrome.exe", "mail.google.com").allowed
    p.grant("site", "youtube.com")                                                # ordinary sites: subdomains follow
    assert p.site_allowed("music.youtube.com")


def test_grants_add_and_revoke_and_protected_things_cannot_be_granted():
    p = Policy.from_dict({})
    assert p.grant("site", "www.youtube.com") == "youtube.com" and p.site_allowed("music.youtube.com")
    p.revoke("site", "youtube.com")
    assert not p.site_allowed("youtube.com")
    assert p.grant("app", "Spotify", r"shell:AppsFolder\x") == "spotify" and p.approved_apps["spotify"].startswith("shell:")
    assert p.grant("window", "Whatever", "C:\\Program Files\\Chrome\\chrome.exe") == "chrome.exe"
    assert "chrome.exe" in p.ui_extra_processes
    assert p.check_grant("site", "paypal.com").verdict == "deny"                    # payment pages: never
    assert p.check_grant("site", "login.live.com").verdict == "deny"
    assert p.check_grant("window", "cmd.exe").verdict == "deny"                     # shells: never
    assert p.check_grant("window", "User Account Control").verdict == "deny"
    assert p.check_grant("app", "bitwarden").verdict == "deny"
    assert p.check_grant("site", "openrouter.ai").verdict == "allow"


def test_a_site_nobody_approved_is_a_request_not_a_dead_end():
    from taskpilot.tools import get_registry
    from taskpilot.protocol import AppToolCall
    p = Policy.from_dict({"require_site_approval": True})
    d = p.evaluate(AppToolCall(name="browser.open_url", args={"url": "https://www.youtube.com/watch?v=x"}))
    assert d.verdict == "deny" and d.access == {"kind": "site", "target": "youtube.com"}
    p.grant("site", "youtube.com")
    assert p.evaluate(AppToolCall(name="browser.open_url", args={"url": "https://music.youtube.com/"})).allowed
    bank = p.evaluate(AppToolCall(name="browser.open_url", args={"url": "https://www.paypal.com/signin"}))
    assert bank.verdict == "deny" and bank.access is None                           # not offered for approval at all


# ------------------------------------------------------------------ the helper applies only what the user approved
async def test_grant_access_is_applied_remembered_or_revoked_with_the_task(make_env):
    e = make_env([])
    saved = []
    e.executor.persist = lambda kind, key, value: saved.append((kind, key, value))

    async def grant(**kw):
        return await e.executor.handle(ActionRequest(action_id=f"g{len(saved)}{kw['target']}", task_id="t1",
                                                     action=GrantAccess(**kw)))
    r = await grant(kind="site", target="www.youtube.com")
    assert r.ok and "youtube.com" in e.policy.ui_allowed_hosts and saved == []      # this time only
    r = await grant(kind="site", target="openrouter.ai", remember=True)
    assert r.ok and saved == [("site", "openrouter.ai", None)]
    r = await grant(kind="app", target="Spotify", remember=True)
    assert r.ok and e.policy.approved_apps["spotify"].startswith("shell:") and saved[-1][0] == "app"
    e.executor.end_task("t1")                                                      # the task ended
    assert "youtube.com" not in e.policy.ui_allowed_hosts                          # 'this time' was taken back
    assert "openrouter.ai" in e.policy.ui_allowed_hosts and "spotify" in e.policy.approved_apps   # saved ones stay
    assert not (await grant(kind="site", target="paypal.com")).ok                  # protected: refused
    nf = await grant(kind="app", target="No Such App")
    assert nf.status == "error" and "could not find" in nf.error


# ------------------------------------------------------------------ the full flows through the orchestrator
async def test_an_unapproved_app_asks_the_user_then_opens_it(make_env):
    """'Open Spotify' used to die with 'not an approved application'. Now the user is asked."""
    e = make_env([act("launch_application", app_id="spotify"), act("launch_application", app_id="spotify"),
                  done("Spotify is open: launch verified")], screenshot_mode="auto")
    e.access_choice = "task"
    tid, t = await e.run("Open Spotify and play something")
    assert t["status"] == "completed", t
    assert len(e.asked) == 1 and e.asked[0]["access"] == {"kind": "app", "target": "spotify"}
    assert "Spotify" in e.asked[0]["question"] or "spotify" in e.asked[0]["question"]
    assert any(c[0] == "launch" for c in e.backend.calls)                           # it really launched
    assert "spotify" not in e.policy.approved_apps                                  # 'this time': taken back at the end


async def test_declining_access_keeps_the_task_alive_and_never_asks_twice(make_env):
    e = make_env([act("launch_application", app_id="spotify"), act("launch_application", app_id="spotify"),
                  {"outcome": {"outcome": "cannot_proceed", "message": "no spotify"}}], screenshot_mode="auto")
    e.access_choice = "no"
    tid, t = await e.run("Open Spotify")
    assert len(e.asked) == 1                                                        # the second try did not ask again
    notes = " ".join(e.model.contexts[-1].notes)
    assert "declined access" in notes and "do not try it again" in notes.lower().replace("not ", "not ") or "already declined" in notes
    assert not any(c[0] == "launch" for c in e.backend.calls)


async def test_a_website_is_asked_for_then_opened_and_always_is_saved(make_env):
    url = "https://www.youtube.com/results?search_query=relaxing"
    e = make_env([act("browser.open_url", url=url), act("browser.open_url", url=url + "&x=1"), done("Opened: page open")],
                 screenshot_mode="auto")
    e.policy.require_site_approval = True
    saved = []
    e.executor.persist = lambda kind, key, value: saved.append((kind, key))
    e.access_choice = "always"
    tid, t = await e.run("Play something relaxing on YouTube")
    assert t["status"] == "completed", t
    assert e.asked[0]["access"] == {"kind": "site", "target": "youtube.com"}
    assert ("url", url + "&x=1") in e.backend.calls
    assert saved == [("site", "youtube.com")] and "youtube.com" in e.policy.ui_allowed_hosts


async def test_a_browser_window_the_user_has_not_approved_asks_about_the_site_not_the_whole_browser(make_env):
    e = make_env([act("ui.snapshot", window="Other app"), act("ui.snapshot", window="Other app"),
                  done("Looked at the page")], screenshot_mode="auto")
    e.backend.ui.windows["Other app"]["process"] = "chrome.exe"
    e.backend.hosts["Other app"] = "www.openrouter.ai"
    e.access_choice = "task"
    tid, t = await e.run("g")
    assert e.asked[0]["access"] == {"kind": "site", "target": "openrouter.ai"}


async def test_questions_with_choices_reach_the_user_and_the_answer_comes_back(make_env):
    e = make_env([ask("Where should I play it?", "YouTube", "Spotify"), done("Playing on YouTube: confirmed")],
                 screenshot_mode="auto")
    e.question_reply = "YouTube"
    tid, t = await e.run("Play something relaxing")
    assert t["status"] == "completed", t
    q = e.asked[0]
    assert q["kind"] == "question" and [o["label"] for o in q["options"]] == ["YouTube", "Spotify"] and q["allow_text"]
    assert any("YouTube" in n and "answered" in n for n in e.model.contexts[-1].notes)


async def test_unanswered_questions_end_the_task_cleanly_not_forever(make_env):
    e = make_env([ask("Which one?")], screenshot_mode="auto", question_timeout_s=0.3)
    tid, t = await e.run("g")
    assert t["status"] == "failed" and "no answer" in t["error"]


async def test_the_model_cannot_grant_itself_access(make_env):
    attack = act("grant_access", kind="site", target="evil.example")
    e = make_env([attack, attack, attack, attack, attack], screenshot_mode="auto")
    tid, t = await e.run("g")
    assert t["status"] == "failed" and "grant itself access" in t["error"]
    assert "evil.example" not in e.policy.ui_allowed_hosts and e.asked == []


async def test_cancelling_while_waiting_for_access_stops_the_task(make_env):
    import asyncio
    e = make_env([act("launch_application", app_id="spotify")], screenshot_mode="auto")
    e.access_choice = None
    tid = e.orch.submit("dev1", "Open Spotify")
    for _ in range(300):
        await asyncio.sleep(0.01)
        if e.orch.pending_approval(tid):
            break
    assert e.orch.pending_approval(tid)["kind"] == "access"
    await e.orch.cancel(tid)
    t = await asyncio.wait_for(e.orch.wait(tid), 5)
    assert t["status"] == "cancelled"


def test_answers_must_match_the_offered_options(make_env, tmp_path):
    from taskpilot.orchestrator import Orchestrator
    e = make_env([])
    assert e.orch.answer("nothing-pending", "x") is False


# ------------------------------------------------------------------ the app's API
def test_the_app_api_exposes_and_answers_questions(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from taskpilot.devices import DeviceHub
    from taskpilot.model import ScriptedRuntime
    from taskpilot.orchestrator import Config, Orchestrator
    from taskpilot.server import create_app
    from taskpilot.store import Store
    from conftest import FakeBackend, Loopback, make_policy
    from taskpilot.agent.executor import Executor

    store, hub, policy = Store(), DeviceHub(), make_policy(tmp_path)
    store.enroll_device("dev1", "PC")
    hub.register("dev1", Loopback(Executor(FakeBackend(), policy)))
    model = ScriptedRuntime([ask("Where should I play it?", "YouTube", "Spotify"), done("Playing on YouTube: confirmed")])
    orch = Orchestrator(store, hub, model, policy, Config(settle_delay_s=0, action_timeout_s=2, screenshot_mode="auto"))
    app = create_app(store, hub, orch, "admin")
    with TestClient(app) as c:
        code = c.post("/pairing", headers={"X-API-Key": "admin"}).json()["code"]
        h = {"Authorization": "Bearer " + c.post("/v1/pair", json={"code": code, "name": "me"}).json()["token"]}
        tid = c.post("/v1/tasks", json={"goal": "Play something relaxing"}, headers=h).json()["task_id"]
        for _ in range(300):
            v = c.get(f"/v1/tasks/{tid}", headers=h).json()
            if v["pending_approval"]:
                break
            time.sleep(0.02)
        pa = v["pending_approval"]
        assert pa["kind"] == "question" and pa["allow_text"] and [o["id"] for o in pa["options"]] == ["YouTube", "Spotify"]
        assert c.post(f"/v1/tasks/{tid}/answer", json={"choice": "Nope"}, headers=h).status_code == 409      # not an offered option
        assert c.post(f"/v1/tasks/{tid}/answer", json={}, headers=h).status_code == 409                      # empty
        assert c.post(f"/v1/tasks/{tid}/answer", json={"text": "on YouTube please"}, headers=h).json() == {"answered": True}
        for _ in range(300):
            v = c.get(f"/v1/tasks/{tid}", headers=h).json()
            if v["terminal"]:
                break
            time.sleep(0.02)
        assert v["status"] == "completed"
        assert c.post(f"/v1/tasks/{tid}/answer", json={"text": "late"}, headers=h).status_code == 409        # nothing pending


async def test_focusing_a_browser_window_the_user_has_not_approved_asks_instead_of_failing(make_env):
    """The exact failure from real use: 'no approved window matches Chrome' with no way forward."""
    e = make_env([act("windows.focus", title="Other app"), act("windows.focus", title="Other app"), done("Focused it")],
                 screenshot_mode="auto")
    e.policy.cloud_safe = True
    e.backend.windows.append({"title": "Other app", "process": "chrome.exe", "pid": 7})
    e.backend.hosts["Other app"] = "www.youtube.com"
    e.access_choice = "task"
    tid, t = await e.run("g")
    assert e.asked and e.asked[0]["access"] == {"kind": "site", "target": "youtube.com"}
    assert "youtube.com" not in e.policy.ui_allowed_hosts                      # 'this time' ended with the task
