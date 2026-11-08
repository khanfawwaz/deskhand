import os

from taskpilot.agent import uia
from taskpilot.playbooks import playbooks_for, requirements_for
from taskpilot.protocol import ActionAdapter, ActionRequest
from taskpilot.tools import get_registry

from conftest import act


def call(tool_name, /, **args):
    return ActionAdapter.validate_python({"tool": "app_tool", "name": tool_name, "args": args})


async def run(env, tool_name, /, **args):
    return await env.executor.handle(ActionRequest(action_id=os.urandom(4).hex(), task_id="t", action=call(tool_name, **args)))


# ------------------------------------------------------------------ which text counts as "the page"

class _R:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class _C:
    def __init__(self, rect):
        self.BoundingRectangle = _R(*rect)


def el(role, name, rect):
    return {"role": role, "name": name, "ctrl": _C(rect)}


DOC = el("Document", "Chat - Google Chrome", (0, 100, 1500, 900))


def test_select_blocks_keeps_the_answer_and_drops_sidebar_chrome_duplicates_and_fragments():
    answer1 = "You do not need to spend more than Rs 1,400: the DS3225 class servo is the cheapest sensible pick."
    answer2 = "Do not power it from the Arduino 5V pin; use a separate 6-7V supply that can deliver at least 3A."
    els = [
        DOC,
        el("ListItem", "Assignment answers about my previous homework (private chat title)", (10, 200, 250, 230)),   # sidebar
        el("Text", "ChatGPT can make mistakes. Check important info.", (400, 860, 1100, 880)),                         # boilerplate
        el("Text", "short text", (400, 300, 600, 320)),                                                              # too short
        el("Text", "Toolbar text outside the page that is quite long indeed", (400, 20, 1100, 60)),                  # not in doc
        el("Group", "what is the best cheapest 25 kg Servo motor i can find", (500, 200, 1300, 240)),               # the question
        el("Text", answer1, (400, 300, 1300, 340)),
        el("Text", answer1, (400, 300, 1300, 340)),                                                                  # duplicate
        el("Text", answer1[:50] + " fragment", (400, 300, 700, 320)),                                                # not a piece of answer1
        el("Text", answer1[10:70], (400, 300, 700, 320)),                                                            # piece of answer1
        el("Group", answer2, (400, 400, 1300, 440)),
    ]
    out = uia._select_blocks(els, uia._biggest_document(els))
    assert out == ["what is the best cheapest 25 kg Servo motor i can find", answer1, answer1[:50] + " fragment", answer2]
    assert not any("homework" in b or "mistakes" in b or "Toolbar" in b for b in out)


def test_product_cards_are_read_but_sidebar_hyperlinks_never_are():
    card = "DSServo RDS3225 25kg Metal Gear Servo Arduino Compatible Servo Motor 1 | REES52"
    els = [
        DOC,
        el("Image", card, (500, 500, 700, 600)),                                         # card image
        el("Button", "Open " + card, (500, 500, 700, 600)),                              # card button (longer: wins)
        el("Button", "Copy", (1200, 300, 1250, 330)),                                    # short UI button
        el("Button", "Add files and more attachments to this prompt", (20, 850, 60, 890)),   # sidebar-side control
        el("Hyperlink", "Private chat title: Assignment answers for my course", (10, 300, 400, 330)),
        el("Hyperlink", "A long product hyperlink in the main content area that is skipped", (500, 700, 1300, 730)),
    ]
    out = uia._select_blocks(els, uia._biggest_document(els))
    assert out == ["Open " + card]                                                     # image text folded into the button
    assert not any("Private chat title" in b or "Copy" == b for b in out)


# ------------------------------------------------------------------ waiting for a streaming answer

class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


def run_page_text(frames, **kw):
    """frames: list of (blocks, busy) returned by successive reads; the last one repeats forever."""
    clk, i = Clock(), [0]

    def read(win):
        f = frames[min(i[0], len(frames) - 1)]
        i[0] += 1
        return f
    return uia.page_text({}, _read=read, _sleep=clk.sleep, _now=clk.now, **kw), i[0], clk.t


def test_waits_through_streaming_and_returns_the_finished_answer():
    stream = [(["Q", "The best"], True), (["Q", "The best cheap"], True), (["Q", "The best cheap servo is X"], True)]
    done = (["Q", "The best cheap servo is X, about Rs 1,200."], False)
    out, reads, t = run_page_text(stream + [done], min_wait_s=2, quiet_s=3, max_wait_s=60)
    assert out["stable"] is True and out["still_generating"] is False
    assert out["page_text"].endswith("about Rs 1,200.")                 # the COMPLETE text, not a half-streamed one
    assert t >= 3                                                       # waited for it to stay unchanged


def test_still_generating_button_blocks_completion_even_if_text_pauses():
    out, _, t = run_page_text([(["Q", "partial answer that pauses"], True)], min_wait_s=1, quiet_s=2, max_wait_s=10)
    assert out["stable"] is False and out["still_generating"] is True and t >= 10      # gave up at max_wait, honestly


def test_min_wait_is_respected_when_text_never_changes():
    """An answer that has not started yet looks 'stable': always wait min_wait_s first."""
    out, _, t = run_page_text([(["Q only"], False)], min_wait_s=8, quiet_s=2, max_wait_s=60)
    assert out["stable"] is True and t >= 8


def test_long_pages_return_the_end_where_the_newest_content_is():
    big = ["a" * 1000, "b" * 1000, "THE ANSWER"]
    out, _, _ = run_page_text([(big, False)], min_wait_s=0, quiet_s=1, max_wait_s=5, max_chars=300)
    assert out["truncated"] is True and out["page_text"].endswith("THE ANSWER") and len(out["page_text"]) == 300
    assert out["chars"] == len("\n".join(big))


# ------------------------------------------------------------------ the tool

async def test_read_page_tool_returns_text_for_approved_windows_only(make_env):
    e = make_env([])
    r = await run(e, "ui.read_page", window="Contact form")
    assert r.ok and "DS3225" in r.data["page_text"] and r.data["stable"] is True
    assert get_registry().get("ui.read_page").verify(None, r.data)[0]
    bad = await run(e, "ui.read_page", window="Other app")
    assert bad.status == "denied" and "not an approved" in bad.error          # asks the user instead of failing
    assert bad.data["needs_access"]["kind"] == "window"
    ok, why = get_registry().get("ui.read_page").verify(None, {"page_text": "x", "chars": 1, "stable": False})
    assert ok and "still changing" in why


def test_read_page_arguments_are_bounded(tmp_path):
    from conftest import make_policy
    p = make_policy(tmp_path)
    assert p.evaluate(call("ui.read_page", window="x", max_wait_s=90)).allowed
    for bad in ({"max_wait_s": 500}, {"max_chars": 50000}, {"quiet_s": 0}, {"min_wait_s": -1}):
        assert p.evaluate(call("ui.read_page", **bad)).verdict == "deny", bad


async def test_a_long_wait_gets_a_long_enough_dispatch_timeout(make_env):
    e = make_env([act("ui.read_page", window="Contact form", max_wait_s=60), {"outcome": {"outcome": "cannot_proceed", "message": "x"}}],
                 screenshot_mode="auto")
    seen = []
    orig = e.conn.execute

    async def spy(req, timeout):
        if req.action.tool == "app_tool":
            seen.append((req.action.name, timeout))
        return await orig(req, timeout)
    e.conn.execute = spy
    await e.run("read it")
    assert ("ui.read_page", 75.0) in seen                               # 60 s of waiting + 15 s margin, not the default 2/30 s


# ------------------------------------------------------------------ "done" must contain what was read

CHAT_GOAL = "Open claude and ask what is the best cheapest 25 kg Servo motor i can find"
LONG = "Best cheap pick: DS3225 around Rs 1,200 (25 kg-cm at 6.8V). " + "Power it from a separate 6-7V supply of at least 3A; " * 3


def done_msg(message, evidence="read the finished answer on the page"):
    return {"outcome": {"outcome": "done", "message": message, "evidence": evidence}}


async def test_a_chat_task_cannot_finish_before_reading_the_answer(make_env):
    e = make_env([done_msg(LONG), act("ui.read_page", window="Contact form"), done_msg(LONG)], screenshot_mode="auto")
    tid, t = await e.run(CHAT_GOAL)
    assert t["status"] == "completed", t
    assert any("you have not read the result yet" in n and "ui.read_page" in n for n in e.model.contexts[1].notes)
    assert t["result"] == LONG                                          # the answer IS the task result


async def test_a_chat_task_must_put_the_answer_in_its_message(make_env):
    e = make_env([act("ui.read_page", window="Contact form"), done_msg("I got an answer."), done_msg(LONG)], screenshot_mode="auto")
    tid, t = await e.run(CHAT_GOAL)
    assert t["status"] == "completed" and t["result"] == LONG
    assert any("must CONTAIN the answer" in n for n in e.model.contexts[2].notes)


async def test_a_model_that_never_reads_fails_instead_of_faking_success(make_env):
    e = make_env([done_msg(LONG)] * 6, screenshot_mode="auto")
    tid, t = await e.run(CHAT_GOAL)
    assert t["status"] == "failed" and "completion not verifiable" in t["error"]


async def test_unrelated_tasks_are_not_burdened_by_the_chat_rule(make_env):
    e = make_env([act("press_key", key="a"), done_msg("typed a", "the key was pressed")], screenshot_mode="auto")
    tid, t = await e.run("press the a key")
    assert t["status"] == "completed"


def test_requirements_follow_the_playbooks():
    assert requirements_for("Open chatgpt and ask something")[0]["requires"] == ("ui.read_page",)
    assert requirements_for(CHAT_GOAL)[0]["name"] == "Claude"
    news = requirements_for("Research on what is the new AI news today")
    assert set(news[0]["requires"]) == {"ui.snapshot", "ui.read_page"}
    assert requirements_for("Open youtube and play the latest video") == []
    assert requirements_for("press the a key") == []
    assert "ui.read_page" in playbooks_for("Open claude and ask") and "ui.read_page" in playbooks_for("Open chatgpt and ask")
