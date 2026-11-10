"""Sharing the computer: Deskhand waits for the owner to pause, works in the background, and puts things back."""
import asyncio
import threading
import time

from taskpilot.agent.polite import Politeness, needs_foreground
from taskpilot.agent.executor import Executor
from taskpilot.policy import Policy
from taskpilot.protocol import ActionRequest, AppToolCall, Click, CaptureScreen, TypeText

from conftest import FakeBackend, make_policy


class Sim:
    """A fake clock and a person who types until `until`, then stops."""

    def __init__(self, until=0.0):
        self.t, self.until = 0.0, until

    def clock(self): return self.t
    def sleep(self, dt): self.t += dt
    def idle(self): return self.t - min(self.t, self.until) if self.until else 99.0


def polite(sim, **kw):
    return Politeness(idle_s=1.0, max_wait_s=kw.pop("max_wait_s", 30), poll_s=0.25, idle_source=sim.idle,
                      clock=sim.clock, sleep=sim.sleep, **kw)


# ------------------------------------------------------------------ the manners
def test_nothing_is_delayed_when_nobody_is_using_the_computer():
    s = Sim()
    p = polite(s)
    assert p.wait_until_idle() and s.t == 0.0 and p.waited_total == 0.0


def test_it_waits_while_the_person_is_busy_and_goes_on_after_they_pause():
    s = Sim(until=3.0)                                   # typing until t=3, then still
    states = []
    p = polite(s, on_state=states.append)
    assert p.wait_until_idle()
    assert 3.9 <= s.t <= 4.3                             # 3 s of typing + the 1 s pause it insists on
    assert states == ["waiting", "working"]


def test_it_gives_up_with_a_clear_answer_if_the_person_never_pauses():
    s = Sim(until=1e9)
    states = []
    p = polite(s, max_wait_s=5, on_state=states.append)
    assert p.wait_until_idle() is False and 5 <= s.t < 6 and states[-1] == "idle"


def test_its_own_keystrokes_are_not_mistaken_for_the_person():
    s = Sim()
    p = polite(s)
    s.t = 10.0
    p.idle_source = lambda: 0.05                          # Windows says: input a moment ago
    assert p.user_idle_s() < 1.0                          # nobody said it was us yet: looks like a person
    p.mark_own_input()
    assert p.user_idle_s() >= p.idle_s                    # now it is known to be ours: the person is still idle


def test_a_person_using_the_computer_right_after_deskhand_still_counts():
    s = Sim()
    p = polite(s)
    s.t = 10.0
    p.mark_own_input()
    s.t = 11.0
    p.idle_source = lambda: 0.2                           # input 0.2 s ago: after our input (1 s ago): that is the person
    assert p.user_idle_s() < 0.5


def test_switched_off_means_no_waiting_at_all():
    s = Sim(until=1e9)
    p = Politeness(enabled=False, idle_source=s.idle, clock=s.clock, sleep=s.sleep)
    assert p.wait_until_idle() and s.t == 0.0


def test_the_owners_window_is_remembered_once_and_put_back_when_they_are_idle():
    s = Sim()
    p = polite(s)
    saved, restored = [], []
    p.remember("t1", lambda: saved.append(1) or {"hwnd": 5})
    p.remember("t1", lambda: saved.append(2) or {"hwnd": 6})        # the second one must not overwrite the first
    assert saved == [1]
    assert p.restore("t1", restored.append) and restored == [{"hwnd": 5}]
    assert not p.restore("t1", restored.append)                     # only once


def test_nothing_is_put_back_while_the_owner_is_busy_or_after_they_moved_on():
    s = Sim(until=1e9)
    p = polite(s)
    restored = []
    p.remember("t", lambda: {"hwnd": 5})
    s.t = 3.0
    assert not p.restore("t", restored.append) and restored == []   # busy: do not pull the window away from them
    q = polite(Sim(until=2.0))
    q.remember("t", lambda: {"hwnd": 5})
    assert q.wait_until_idle("t")                                    # they used the computer while we waited...
    assert not q.restore("t", restored.append)                      # ...so what we saved is stale and is dropped


def test_which_actions_take_over_the_foreground():
    assert needs_foreground(Click(x=1, y=2)) and needs_foreground(TypeText(text="x"))
    assert needs_foreground(AppToolCall(name="windows.focus", args={})) and needs_foreground(AppToolCall(name="browser.open_url", args={}))
    for name in ("web.open", "web.links", "web.click", "web.search", "ui.snapshot", "ui.click", "ui.read", "files.read"):
        assert not needs_foreground(AppToolCall(name=name, args={})), name
    assert not needs_foreground(CaptureScreen())


# ------------------------------------------------------------------ in the executor
def make(tmp_path, idle=99.0, **pol):
    policy = make_policy(tmp_path)
    for k, v in pol.items():
        setattr(policy, k, v)
    b = FakeBackend()
    b.idle = idle
    ex = Executor(b, policy)
    ex.polite.poll_s = 0.02
    return b, ex


def req(action, n=[0], task="t1"):
    n[0] += 1
    return ActionRequest(action_id=f"p{n[0]}", task_id=task, action=action)


async def test_a_click_waits_for_the_owner_to_pause_then_goes_ahead(tmp_path):
    b, ex = make(tmp_path, idle=0.0, polite_idle_s=0.2, polite_max_wait_s=5)
    threading.Timer(0.3, lambda: setattr(b, "idle", 99.0)).start()
    t0 = time.monotonic()
    r = await ex.handle(req(Click(x=5, y=5)))
    assert r.status == "ok" and time.monotonic() - t0 >= 0.25
    assert ("click", 5, 5, "left", 1) in b.calls


async def test_if_the_owner_never_pauses_the_step_is_not_done_and_the_reason_is_plain(tmp_path):
    b, ex = make(tmp_path, idle=0.0, polite_idle_s=0.2, polite_max_wait_s=0.3)
    r = await ex.handle(req(Click(x=5, y=5)))
    assert r.status == "error" and "using the computer" in r.error and not [c for c in b.calls if c[0] == "click"]


async def test_background_work_is_never_delayed_even_while_the_owner_is_busy(tmp_path):
    b, ex = make(tmp_path, idle=0.0, polite_idle_s=0.2, polite_max_wait_s=0.2)
    b.ui.windows["Calculator"] = {"process": "x.exe", "pid": 1}
    for name, args in (("ui.snapshot", {"window": "Calculator"}), ("ui.click", {"window": "Calculator", "name": "Seven", "role": "Button"}),
                       ("web.url", {})):
        t0 = time.monotonic()
        r = await ex.handle(req(AppToolCall(name=name, args=args)))
        assert time.monotonic() - t0 < 0.15, name                     # nobody waited for the person to stop
    assert r is not None


async def test_the_owners_window_comes_back_when_the_task_ends(tmp_path):
    b, ex = make(tmp_path)
    await ex.handle(req(Click(x=1, y=1), task="tA"))
    await ex.handle(req(TypeText(text="hi"), task="tA"))
    assert b.ctx_calls == [("capture_ctx",)]      # saved once, before the first step
    ex.end_task("tA")
    assert ("restore_ctx", 7) in b.ctx_calls


async def test_nothing_is_pulled_back_if_the_owner_is_at_the_keyboard_when_the_task_ends(tmp_path):
    b, ex = make(tmp_path)
    await ex.handle(req(Click(x=1, y=1), task="tB"))
    await asyncio.sleep(0.5)
    b.idle = 0.1                                                        # the person touched the mouse AFTER Deskhand's last click
    ex.end_task("tB")
    assert not [c for c in b.ctx_calls if c[0] == "restore_ctx"]


async def test_the_whole_thing_can_be_switched_off(tmp_path):
    b, ex = make(tmp_path, idle=0.0, polite=False)
    r = await ex.handle(req(Click(x=5, y=5)))
    assert r.status == "ok" and not b.ctx_calls


# ------------------------------------------------------------------ looking at an approved window without bringing it forward
async def test_privacy_mode_captures_the_approved_window_where_it_is(tmp_path):
    b, ex = make(tmp_path)
    ex.policy.cloud_safe = True
    b.can_capture_behind = True
    b.active = "Somebody's private chat - Chrome"; b.active_process = "chrome.exe"      # the owner's own window is in front
    b.windows = [{"title": "Calculator", "process": "ApplicationFrameHost.exe", "pid": 3},
                 {"title": "Somebody's private chat - Chrome", "process": "chrome.exe", "pid": 4}]
    r = await ex.handle(req(CaptureScreen()))
    assert r.status == "ok" and r.screenshot.image_b64 == "BEHIND"
    assert ("shot_titled", "Calculator") in b.calls
    assert not [c for c in b.calls if c[0] == "focus"]                # nothing was brought forward
    assert "private chat" not in str(r.model_dump())                  # and the owner's window never reached the picture or the answer


async def test_without_background_capture_it_only_brings_a_window_forward_when_the_owner_is_idle(tmp_path):
    b, ex = make(tmp_path, polite_idle_s=2.5)
    ex.policy.cloud_safe = True
    b.active = "Desktop"
    b.windows = [{"title": "Calculator", "process": "ApplicationFrameHost.exe", "pid": 3}]
    b.idle = 0.1                                                        # the owner is working: do not steal the front
    r = await ex.handle(req(CaptureScreen()))
    assert r.status == "denied" and not [c for c in b.calls if c[0] == "focus"]
    b.idle = 99.0                                                       # now nobody is using it: bringing it forward is harmless
    r = await ex.handle(req(CaptureScreen()))
    assert [c for c in b.calls if c[0] == "focus"]


def test_the_policy_knobs_have_sane_defaults():
    p = Policy.from_dict({})
    assert p.polite is True and p.polite_idle_s == 2.5 and p.polite_max_wait_s == 45.0
    q = Policy.from_dict({"polite": False, "polite_idle_s": 1, "polite_max_wait_s": 9})
    assert (q.polite, q.polite_idle_s, q.polite_max_wait_s) == (False, 1.0, 9.0)
