"""The Pilot score: a pass is most of it, efficiency/speed/cleanliness the rest, a false success is zero."""
from taskpilot.evals.report import pilot_score, score_run, summarize, to_markdown
from taskpilot.titles import norm, title_has


def run(verdict="PASS", steps=5, budget=40, dur=20, budget_s=240, refused=0, failed=0, tid="t"):
    return {"id": tid, "verdict": verdict, "steps": steps, "budget_steps": budget, "duration_s": dur, "budget_s": budget_s,
            "refused": refused, "failed_steps": failed, "failure": "", "goal": "g", "status": "completed", "error": "",
            "result": "", "checks": [], "trace": [], "category": "c"}


def test_a_clean_fast_pass_scores_near_the_top_and_a_wasteful_one_less():
    assert score_run(run(steps=2, dur=5)) >= 95
    assert score_run(run(steps=38, dur=200, refused=5, failed=5)) < score_run(run(steps=5)) < 100
    assert score_run(run(steps=38, dur=230)) >= 60                       # even a slow pass is still a pass


def test_failures_score_low_and_a_false_success_scores_zero():
    assert score_run(run("FAIL")) == 0 and score_run(run("FALSE_SUCCESS")) == 0
    assert 0 < score_run(run("PARTIAL")) < 60


def test_the_headline_is_the_mean_and_the_report_shows_it():
    rs = [run(tid="a", steps=2, dur=5), run("FALSE_SUCCESS", tid="b")]
    assert pilot_score(rs) == round((score_run(rs[0]) + 0) / 2)
    s = summarize(rs)
    assert s["score"] == pilot_score(rs) and s["tasks"]["b"]["score"] == 0
    assert f"Pilot score: {s['score']}/100" in to_markdown(rs)
    assert pilot_score([]) == 0


def test_window_titles_ignore_the_notification_counter_and_case():
    assert norm("(349) SSundeePlus - YouTube") == "ssundeeplus - youtube"
    assert title_has("(350) SSundeePlus - YouTube - Google Chrome", "(349) ssundeeplus")
    assert title_has("Inbox (3) - Gmail", "inbox (3)")                       # a counter that is not a prefix is left alone
    assert not title_has("Notepad", "") and not title_has("Notepad", "chrome")
