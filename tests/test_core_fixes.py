import json

import pytest

from taskpilot.grounding import distinctive, grounding_score
from taskpilot.memory import hints_for, summarize_run, tokens

from conftest import act, done


def done2(message, evidence):
    return {"outcome": {"outcome": "done", "message": message, "evidence": evidence}}

PAGE = "The best cheap pick is the DS3225 digital servo at about Rs 1,200 on Robu.in; power it from a separate 6-7V supply."


# ------------------------------------------------------------------ grounding: is a claim supported by what was read?

def test_grounding_scores_supported_and_unsupported_claims():
    seen = [PAGE, "window title: Servo guide - Google Chrome"]
    assert grounding_score("The page recommends the DS3225 servo at about Rs 1,200", seen) == 1.0    # paraphrase is fine
    assert grounding_score("The page lists a Raspberry Pi Pico kit costing $45 with free shipping", seen) == 0.0
    assert grounding_score("", seen) is None


def test_quoted_facts_must_appear_verbatim():
    seen = [PAGE]
    assert grounding_score('It says "separate 6-7V supply"', seen) == 1.0                               # verbatim quote found
    assert grounding_score('It says "water cooled brushless gearbox"', seen) == 0.0      # invented quote: always 0
    assert grounding_score('The title was "SERVO   guide"', ["window title: Servo guide - Chrome"], min_corpus=10) == 1.0   # case/space tolerant


def test_too_little_to_judge_is_not_a_failure():
    assert grounding_score("The DS3225 is cheap and good value for money", ["tiny"]) is None            # nothing observed
    assert grounding_score("done ok", [PAGE]) is None                                                    # no specifics to judge
    assert grounding_score("Typed a, file absent as expected", [PAGE]) is None                          # plain words: paraphrase-prone
    assert distinctive("It is at 1,200 Rs on Robu.in") >= {"1,200", "robu.in"}
    assert distinctive("The Dynamixel XM430 costs $329") == {"dynamixel", "xm430", "$329"}


# ------------------------------------------------------------------ the completion gate uses it

async def test_a_claim_not_supported_by_anything_read_is_rejected_then_accepted_when_grounded(make_env):
    e = make_env([act("ui.read_page", window="Contact form"),
                  done("The page shows a Raspberry Pi Pico kit costing $45 with free shipping worldwide"),
                  done("The page recommends the DS3225 digital servo at about Rs 1,200")], screenshot_mode="auto")
    e.backend.page_text = PAGE
    tid, t = await e.run("read the page about servos")
    assert t["status"] == "completed", t
    assert any("not supported by anything you actually read" in n for n in e.model.contexts[2].notes)
    rejected = [a for a in e.store.list_actions(tid) if a["tool"] == "done"]
    assert len(rejected) == 1 and rejected[0]["status"] == "denied" and "grounding evidence=" in rejected[0]["decision"]
    assert "grounding evidence=" in [x for x in e.store.transitions(tid) if x["to_state"] == "verifying"][-1]["note"]


async def test_a_false_success_claim_cannot_finish_at_all(make_env):
    fake = done("The page confirms the Raspberry Pi Pico kit costing $45 was ordered with free shipping")
    e = make_env([act("ui.read_page", window="Contact form")] + [fake] * 5, screenshot_mode="auto")
    e.backend.page_text = PAGE
    tid, t = await e.run("read the page about servos")
    assert t["status"] == "failed" and "completion not verifiable" in t["error"]


async def test_vision_only_tasks_are_not_blocked_by_grounding(make_env):
    """Nothing textual was observed, so the claim cannot be graded and is accepted (the other gates still apply)."""
    e = make_env([act("press_key", key="a"), done("The editor now shows the letter a typed in the document")], screenshot_mode="auto")
    tid, t = await e.run("press the a key")
    assert t["status"] == "completed"


CHAT_GOAL = "Open claude and ask what is the best cheapest 25 kg Servo motor i can find"


async def test_the_answer_in_the_message_must_come_from_what_was_read(make_env):
    made_up = ("Best cheap pick: the Dynamixel XM430 brushless industrial actuator at $329 with an aluminium housing, "
               "plus a CNC machined bracket kit and a liquid cooled power distribution board from AliExpress sellers.")
    real = PAGE + " " + PAGE
    ev = "ui.read_page returned the DS3225 recommendation at about Rs 1,200"
    e = make_env([act("ui.read_page", window="Contact form"), done2(made_up, ev), done2(real, ev)], screenshot_mode="auto")
    e.backend.page_text = PAGE
    tid, t = await e.run(CHAT_GOAL)
    assert t["status"] == "completed" and t["result"] == real
    assert any("do not appear in what you read" in n for n in e.model.contexts[2].notes)


# ------------------------------------------------------------------ stall detection

async def test_the_same_failing_action_is_refused_the_third_time(make_env):
    bad = act("ui.click", window="Contact form", name="Nope")
    e = make_env([bad, bad, bad, {"outcome": {"outcome": "cannot_proceed", "message": "giving up"}}], screenshot_mode="auto")
    tid, t = await e.run("g")
    rows = [(a["tool"], a["status"]) for a in e.store.list_actions(tid)]
    assert rows.count(("ui.click", "failed")) == 2 and rows.count(("ui.click", "denied")) == 1
    assert any("tried exactly this twice" in n for n in e.model.contexts[3].notes)


async def test_repeating_a_successful_change_three_times_is_refused(make_env):
    press = act("ui.click", window="Calculator", name="Seven")
    e = make_env([press] * 5 + [{"outcome": {"outcome": "cannot_proceed", "message": "x"}}], screenshot_mode="auto")
    tid, t = await e.run("g")
    assert [c for c in e.backend.calls if c[0] == "ui_click"].__len__() == 3
    assert any("not making progress" in n for n in e.model.contexts[-1].notes)


async def test_endless_looking_around_ends_the_task(make_env):
    looks = [act("ui.snapshot", window="Calculator", query=f"q{i}") for i in range(20)]     # all different: no stall rule
    e = make_env(looks, screenshot_mode="auto", max_idle_steps=6)
    tid, t = await e.run("g", max_actions=40)
    assert t["status"] == "failed" and "no progress" in t["error"]
    assert len([a for a in e.store.list_actions(tid) if a["tool"] == "ui.snapshot"]) == 6


async def test_real_progress_resets_the_idle_counter(make_env):
    script = [act("ui.snapshot", window="Calculator", query="a"), act("ui.snapshot", window="Calculator", query="b"),
              act("ui.click", window="Calculator", name="Seven"),
              act("ui.snapshot", window="Calculator", query="c"), act("ui.snapshot", window="Calculator", query="d"),
              done("Calculator display now shows Display is 7")]
    e = make_env(script, screenshot_mode="auto", max_idle_steps=3)
    tid, t = await e.run("g")
    assert t["status"] == "completed", t


# ------------------------------------------------------------------ experience memory

async def test_a_verified_success_is_remembered_without_anything_sensitive(make_env):
    e = make_env([act("ui.snapshot", window="Contact form"),
                  act("ui.type", window="Contact form", name="Name", text="hunter2 secret"),
                  act("ui.click", window="Calculator", name="Seven"),
                  act("ui.read", window="Calculator", id="CalculatorResults"),
                  done("ui.read returned Display is 7 after pressing Seven")], screenshot_mode="auto")
    tid, t = await e.run("Fill the contact form then press seven in calculator")
    assert t["status"] == "completed", t
    exps = e.store.list_experiences()
    assert len(exps) == 1 and exps[0]["goal"].startswith("Fill the contact form")
    steps = exps[0]["steps"]
    assert steps == ['look at the page', 'type into "Name"', 'click "Seven"', 'read "CalculatorResults"']
    assert "hunter2" not in json.dumps(exps)                                  # typed text is never kept


async def test_similar_goals_get_the_hint_and_unrelated_ones_do_not(make_env):
    e = make_env([act("ui.click", window="Calculator", name="Seven"), done("Calculator display now shows Display is 7")],
                 screenshot_mode="auto")
    await e.run("Press the seven button in the calculator app")
    e.model.script = [{"outcome": {"outcome": "cannot_proceed", "message": "x"}}]
    e.model.contexts.clear()
    await e.run("Press the seven button in calculator please")
    assert '[Experience] A similar task' in e.model.contexts[0].playbook and 'click "Seven"' in e.model.contexts[0].playbook
    assert "verify every step" in e.model.contexts[0].playbook
    e.model.script = [{"outcome": {"outcome": "cannot_proceed", "message": "x"}}]
    e.model.contexts.clear()
    await e.run("Write a poem about the sea")
    assert "[Experience]" not in e.model.contexts[0].playbook


async def test_learning_can_be_turned_off_and_duplicates_are_not_piled_up(make_env):
    script = [act("ui.click", window="Calculator", name="Seven"), done("Calculator display now shows Display is 7")]
    off = make_env(list(script), screenshot_mode="auto", learn=False)
    await off.run("Press seven in calculator")
    assert off.store.list_experiences() == []
    on = make_env(list(script), screenshot_mode="auto")
    await on.run("Press seven in calculator")
    on.model.script = list(script)
    on.backend.ui.reset()
    await on.run("Press seven in calculator")
    assert len(on.store.list_experiences()) == 1


def test_experience_summary_and_hint_matching_unit():
    rows = [{"tool": "browser.open_url", "status": "succeeded", "params": {"name": "browser.open_url",
             "args": {"url": "https://news.google.com/search?q=ai"}}},
            {"tool": "ui.snapshot", "status": "succeeded", "params": {"name": "ui.snapshot", "args": {"query": "headline"}}},
            {"tool": "ui.click", "status": "failed", "params": {"args": {"name": "x"}}},
            {"tool": "capture_screen", "status": "succeeded", "params": {}},
            {"tool": "ui.type", "status": "succeeded", "params": {"args": {"name": "Search", "text": "<redacted>"}}}]
    exp = summarize_run("Research the new AI news today", rows)
    assert exp["steps"] == ["open news.google.com/search?…", 'look at the page (query "headline")', 'type into "Search"']
    assert summarize_run("nothing", [{"tool": "capture_screen", "status": "succeeded"}]) is None
    assert "news.google.com" in hints_for("What is the latest AI news today", [exp])
    assert hints_for("Open youtube and play a video", [exp]) == ""
    assert tokens("Open the AI news") == {"open", "news"}
