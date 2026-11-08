from taskpilot.evals import report
from taskpilot.evals import verifiers as V
from taskpilot.evals.runner import classify_failure, run_spec
from taskpilot.evals.sim import calculator_page, SimBackend, youtube_world
from taskpilot.evals.spec import TaskSpec
from taskpilot.model import ScriptedRuntime
from taskpilot.orchestrator import Config

from conftest import act, done

VIDEOS = [("v3", "Season finale: Tombstone vs Witch Doctor"), ("v2", "Bracket reveal"), ("v1", "Old fight")]
CFG = Config(settle_delay_s=0, ready_poll_s=0.01, ready_timeout_s=0.2, device_wait_s=0.5, confirm_timeout_s=2,
             cancel_grace_s=0.2, action_timeout_s=2, screenshot_mode="auto", learn=False)
OPEN = act("browser.open_url", url="https://www.youtube.com/@BattleBots/videos")


def yt_spec():
    return TaskSpec("t.yt", "Open youtube and play the latest video from battle bots", "web",
                    [V.completed(), V.window_title_has(VIDEOS[0][1], label="newest video open")],
                    windows=["YouTube"], hosts=["youtube.com"], world=lambda: youtube_world(VIDEOS)[0], max_actions=12,
                    timeout_s=10)


async def run(spec, script, **kw):
    return await run_spec(spec, spec.world(), ScriptedRuntime(script), config=CFG, **kw)


async def test_a_correct_run_passes():
    r = await run(yt_spec(), [OPEN, act("ui.click", window="YouTube", name=VIDEOS[0][1]),
                              done(f"Playing {VIDEOS[0][1]} on the BattleBots channel")])
    assert r["verdict"] == "PASS", r
    assert r["failure"] == "" and r["steps"] == 2


async def test_the_harness_catches_a_false_success():
    """Pilot plays the OLDER video and claims success. The independent check must call it FALSE_SUCCESS."""
    r = await run(yt_spec(), [OPEN, act("ui.click", window="YouTube", name=VIDEOS[2][1]),
                              done(f"Now playing {VIDEOS[2][1]} on the BattleBots channel")])
    assert r["status"] == "completed"
    assert r["verdict"] == "FALSE_SUCCESS", r
    bad = [c for c in r["checks"] if c["passed"] is False]
    assert [c["name"] for c in bad] == ["newest video open"]


async def test_giving_up_is_a_fail_with_a_cause():
    r = await run(yt_spec(), [OPEN, {"outcome": {"outcome": "cannot_proceed", "message": "no idea"}}])
    assert r["verdict"] == "FAIL" and r["status"] == "failed" and r["failure"] != ""


async def test_state_right_but_not_claimed_is_partial():
    r = await run(yt_spec(), [OPEN, act("ui.click", window="YouTube", name=VIDEOS[0][1]),
                              {"outcome": {"outcome": "cannot_proceed", "message": "unsure"}}])
    assert r["verdict"] == "PARTIAL"


async def test_files_are_graded_from_disk_not_from_the_claim(tmp_path):
    spec = TaskSpec("t.file", "Create {sandbox}/note.txt containing exactly: hello eval", "files",
                    [V.completed(), V.file_text("note.txt", equals="hello eval")], max_actions=8, timeout_s=10)
    ok = await run_spec(spec, SimBackend({}), ScriptedRuntime(
        [act("files.write", path=str(tmp_path / "note.txt"), text="hello eval"),
         done("files.write created note.txt with hello eval")]), config=CFG, workdir=tmp_path)
    assert ok["verdict"] == "PASS", ok
    (tmp_path / "note.txt").unlink()
    liar = await run_spec(spec, SimBackend({}), ScriptedRuntime(
        [act("files.exists", path=str(tmp_path / "note.txt")),
         done("note.txt exists and contains hello eval")]), config=CFG, workdir=tmp_path)
    assert liar["verdict"] == "FALSE_SUCCESS", liar


async def test_calculator_state_is_read_from_the_app():
    page, _ = calculator_page()
    spec = TaskSpec("t.calc", "add 7 and 5", "desktop",
                    [V.completed(), V.control_value("Calculator", {"id": "CalculatorResults"}, contains="12")],
                    windows=["Calculator"], approved_apps={"calc": "calc.exe"}, max_actions=14, timeout_s=10)
    def steps(*names):
        return [act("ui.click", window="Calculator", name=n) for n in names]
    good = [act("launch_application", app_id="calc"), act("ui.snapshot", window="Calculator")] + \
        steps("Seven", "Plus", "Five", "Equals") + [act("ui.read", window="Calculator", id="CalculatorResults"),
                                                    done("ui.read returned Display is 12")]
    r = await run_spec(spec, SimBackend({}, {"calc": page}), ScriptedRuntime(good), config=CFG)
    assert r["verdict"] == "PASS", r


async def test_unapproved_requests_are_declined_by_default():
    spec = yt_spec()
    r = await run(spec, [OPEN, {"outcome": {"outcome": "cannot_proceed", "message": "x"}}])
    assert r["approvals"] == []                       # nothing asked, nothing granted


def test_failure_taxonomy():
    assert classify_failure("failed", "no progress for 10 steps", []) == "LOOP"
    assert classify_failure("failed", "timed out", []) == "TIMEOUT"
    assert classify_failure("failed", "completion not verifiable", []) == "UNVERIFIED_DONE"
    assert classify_failure("completed", "", []) == ""
    assert classify_failure("failed", "boom", [{"status": "denied"}] * 3) == "POLICY"


def _res(id_, verdict, failure="", steps=3):
    return {"id": id_, "category": "c", "goal": "g", "verdict": verdict, "status": "completed", "failure": failure,
            "duration_s": 5.0, "steps": steps, "model_calls": 3, "refused": 0, "failed_steps": 0, "result": "r",
            "error": "", "approvals": [], "checks": [{"name": "n", "passed": verdict == "PASS", "detail": "d", "hard": True}],
            "trace": [{"seq": 1, "tool": "t", "status": "succeeded", "what": "", "note": ""}]}


def test_report_summary_markdown_and_regressions(tmp_path):
    a = [_res("x", "PASS"), _res("y", "PASS")]
    b = [_res("x", "PASS"), _res("y", "FALSE_SUCCESS", "UNVERIFIED_DONE")]
    s = report.summarize(b)
    assert s["verdicts"] == {"PASS": 1, "FALSE_SUCCESS": 1} and s["false_success_rate"] == 0.5
    md = report.to_markdown(b, {"suite": "sim"})
    assert "FALSE_SUCCESS: y" in md and "1/2 passed" in md
    report.save(a, tmp_path)
    old = report.last_summary(tmp_path)
    c = report.compare(old, s)
    assert [r["task"] for r in c["regressions"]] == ["y"] and c["improvements"] == []
