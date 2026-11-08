"""Reports: JSON + markdown per run, a history log, and run-to-run comparison (regressions)."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

ORDER = ["PASS", "PARTIAL", "INCONCLUSIVE", "FAIL", "FALSE_SUCCESS"]


def summarize(results: list[dict]) -> dict:
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_task[r["id"]].append(r)
    verdicts = Counter(r["verdict"] for r in results)
    n = len(results) or 1
    tasks = {}
    for tid, rs in by_task.items():
        c = Counter(r["verdict"] for r in rs)
        tasks[tid] = {"runs": len(rs), "pass": c["PASS"], "false_success": c["FALSE_SUCCESS"],
                      "pass_rate": round(c["PASS"] / len(rs), 2),
                      "avg_steps": round(sum(r["steps"] for r in rs) / len(rs), 1),
                      "avg_seconds": round(sum(r["duration_s"] for r in rs) / len(rs), 1),
                      "failures": dict(Counter(r["failure"] for r in rs if r["failure"]))}
    return {"runs": len(results), "verdicts": dict(verdicts), "pass_rate": round(verdicts["PASS"] / n, 3),
            "false_success_rate": round(verdicts["FALSE_SUCCESS"] / n, 3),
            "failures": dict(Counter(r["failure"] for r in results if r["failure"])), "tasks": tasks}


def to_markdown(results: list[dict], meta: dict | None = None) -> str:
    s = summarize(results)
    out = ["# Pilot evaluation", ""]
    for k, v in (meta or {}).items():
        out.append(f"- **{k}**: {v}")
    out += ["", f"**{s['verdicts'].get('PASS', 0)}/{s['runs']} passed** "
            f"({s['pass_rate']:.0%}) · false successes: **{s['verdicts'].get('FALSE_SUCCESS', 0)}** "
            f"({s['false_success_rate']:.0%}) · failures by cause: {s['failures'] or 'none'}", "",
            "| task | runs | pass | false success | avg steps | avg s | failure causes |", "|---|---|---|---|---|---|---|"]
    for tid, t in s["tasks"].items():
        out.append(f"| {tid} | {t['runs']} | {t['pass']} | {t['false_success']} | {t['avg_steps']} | "
                   f"{t['avg_seconds']} | {t['failures'] or ''} |")
    for r in sorted(results, key=lambda r: ORDER.index(r["verdict"]) if r["verdict"] in ORDER else 9, reverse=True):
        if r["verdict"] == "PASS":
            continue
        out += ["", f"## {r['verdict']}: {r['id']}  ({r['failure'] or 'no failure class'})",
                f"*goal*: {r['goal']}", f"*status*: {r['status']} · {r['steps']} steps · {r['duration_s']}s"
                + (f" · error: {r['error']}" if r["error"] else ""), ""]
        for c in r["checks"]:
            mark = {True: "ok ", False: "BAD", None: "?? "}[c["passed"]]
            out.append(f"- [{mark}] {c['name']}{'' if c['hard'] else ' (soft)'}: {c['detail']}")
        if r["result"]:
            out += ["", "Pilot said:", "> " + r["result"][:600].replace("\n", "\n> ")]
        out += ["", "```"] + [f"#{t['seq']:<2} {t['tool']:<18} {t['status']:<9} {t['what']:<60} {t['note'][:70]}"
                              for t in r["trace"]] + ["```"]
    return "\n".join(out) + "\n"


def save(results: list[dict], out_dir: Path, meta: dict | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    meta = {"when": stamp, **(meta or {})}
    jp, mp = out_dir / f"eval-{stamp}.json", out_dir / f"eval-{stamp}.md"
    jp.write_text(json.dumps({"meta": meta, "summary": summarize(results), "results": results}, indent=1), encoding="utf-8")
    mp.write_text(to_markdown(results, meta), encoding="utf-8")
    with (out_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"meta": meta, "summary": summarize(results)}) + "\n")
    return {"json": jp, "markdown": mp}


def compare(old: dict, new: dict) -> dict:
    """Per-task change between two summaries: which tasks regressed (pass rate fell) or improved."""
    reg, imp = [], []
    for tid, t in new["tasks"].items():
        o = old["tasks"].get(tid)
        if not o:
            continue
        if t["pass_rate"] < o["pass_rate"] or t["false_success"] > o["false_success"]:
            reg.append({"task": tid, "was": o["pass_rate"], "now": t["pass_rate"]})
        elif t["pass_rate"] > o["pass_rate"]:
            imp.append({"task": tid, "was": o["pass_rate"], "now": t["pass_rate"]})
    return {"regressions": reg, "improvements": imp}


def last_summary(out_dir: Path) -> dict | None:
    h = out_dir / "history.jsonl"
    if not h.exists():
        return None
    lines = [ln for ln in h.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1])["summary"] if lines else None
