"""Skills: what Deskhand has learned about one app or website, kept up to date after every task.

After each task, the actions it took are grouped by the app or site they were about (Spotify, youtube.com, Calculator...).
For each one a SKILL is created or updated:
    open      how it was opened last time it worked
    controls  the controls that actually worked (window, role, name, id) with how often
    flows     step-by-step routes that worked for a goal, merged when goals are similar (shortest/newest route wins)
    pitfalls  steps that failed, dropped again once a later run gets past them
    runs      how many tasks succeeded / failed
The next task that mentions the app, or opens it, is handed the skill as a hint ("verify; apps change").

Safety: a skill is built ONLY from structured facts (tool names, hosts, control names that were really clicked), never from
free text on a page, in a file or typed by the user, and every name is cleaned and length-limited. A malicious page
therefore cannot plant instructions that would be replayed to the model later. Skills are per workspace, listed and
deletable by their owner, and shown to the model as data to verify, not as orders.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from .hygiene import clean, safe
from .hygiene import line as oneline
from .memory import SKIP_TOOLS, summarize_step, tokens
from .policy import base_domain

MAX_CONTROLS, MAX_FLOWS, MAX_PITFALLS, MAX_STEPS = 24, 5, 8, 14


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", (s or "").lower())[:50]


def _args(row: dict) -> dict:
    p = row.get("params") or {}
    a = p.get("args", p) if isinstance(p, dict) else {}
    return a if isinstance(a, dict) else {}


def subject_of_call(tool: str, args: dict) -> tuple[str, str, str] | None:
    """(kind, key, title) when this action OPENS an app or a site; None otherwise."""
    if tool == "launch_application" and args.get("app_id"):
        k = _slug(str(args["app_id"]))
        return ("app", k, clean(args["app_id"], 40)) if k else None
    if tool in ("browser.open_url", "browser.open_private"):
        host = urlparse(str(args.get("url", ""))).hostname or ""
        k = base_domain(host)
        return ("site", k, k) if k else None
    return None


def analyse(rows: list[dict]) -> dict[str, dict]:
    """Group a finished task's actions by the app/site they were about."""
    subs: dict[str, dict] = {}
    current: str | None = None
    for r in rows:
        tool, status = r["tool"], r.get("status")
        if tool in ("done", "invalid", "question"):
            continue
        a = _args(r)
        opened = subject_of_call(tool, a) if status == "succeeded" else None
        if opened:
            kind, key, title = opened
            current = f"{kind}:{key}"
            s = subs.setdefault(current, {"kind": kind, "key": key, "title": title, "open": None, "controls": {},
                                          "steps": [], "failures": [], "worked": set()})
            line = (f"launch_application {clean(a.get('app_id'), 30)}" if kind == "app"
                    else "browser.open_url " + clean(f"https://{urlparse(str(a.get('url',''))).netloc}{urlparse(str(a.get('url',''))).path}", 120))
            s["open"] = line
        elif tool.startswith("ui.") and current is None and a.get("window"):
            key = _slug(str(a["window"]))                         # an app that was already open (Calculator, Notepad)
            if key:
                current = f"window:{key}"
                subs.setdefault(current, {"kind": "window", "key": key, "title": clean(a["window"], 40), "open": None,
                                          "controls": {}, "steps": [], "failures": [], "worked": set()})
        if current is None:
            continue
        s = subs[current]
        step = summarize_step(r) if status == "succeeded" else None
        if step:
            step = oneline(step, 90)
            if not safe(step):                                    # a hostile control name never reaches a stored route
                step = f"{tool.split('.')[-1]} a control (name withheld)"
        if step and (not s["steps"] or s["steps"][-1] != step):
            s["steps"].append(step)
        if status == "succeeded" and tool in ("ui.click", "ui.type", "ui.read"):
            name, cid = clean(a.get("name"), 60), clean(a.get("id"), 40)
            if (name or cid) and safe(name, cid, clean(a.get("window"), 40)):
                win = clean(a.get("window") or "", 40)
                k = (win, name, cid, clean(a.get("role"), 20))
                s["controls"][k] = s["controls"].get(k, 0) + 1
                s["worked"].add(f"{tool}|{name or cid}")
        elif status == "failed" and tool not in SKIP_TOOLS and not tool.startswith("grant"):
            target = clean(a.get("name") or a.get("id") or a.get("title") or a.get("query") or a.get("app_id") or "", 40)
            why = clean(r.get("error") or r.get("verification") or "", 90)
            quoted = f'"{target}"' if target else ""
            if not safe(target, why):
                continue
            s["failures"].append({"key": f"{tool}|{target}", "text": f"{tool} {quoted}: {why}".strip()})
    return subs


def _similar(a: str, b: str, floor: float = 0.6) -> bool:
    x, y = tokens(a), tokens(b)
    return bool(x and y) and len(x & y) / len(x | y) >= floor


def update_skill(old: dict | None, subj: dict, goal: str, succeeded: bool, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    sk = dict(old) if old else {"key": subj["key"], "title": subj["title"], "kind": subj["kind"], "open": None,
                                "controls": [], "flows": [], "pitfalls": [], "runs": {"ok": 0, "fail": 0}, "version": 0}
    sk["runs"] = {"ok": sk["runs"]["ok"] + (1 if succeeded else 0), "fail": sk["runs"]["fail"] + (0 if succeeded else 1)}
    if subj["open"] and succeeded:
        sk["open"] = subj["open"]
    elif subj["open"] and not sk.get("open"):
        sk["open"] = subj["open"]
    # controls: count how often each really worked
    table = {(c["window"], c["name"], c["id"], c["role"]): c["n"] for c in sk["controls"]}
    for k, n in subj["controls"].items():
        table[k] = table.get(k, 0) + n
    top = sorted(table.items(), key=lambda kv: -kv[1])[:MAX_CONTROLS]
    sk["controls"] = [{"window": w, "name": nm, "id": i, "role": ro, "n": n} for (w, nm, i, ro), n in top]
    # flows: keep the best route per kind of goal
    if succeeded and subj["steps"]:
        steps = subj["steps"][:MAX_STEPS]
        for f in sk["flows"]:
            if _similar(f["goal"], goal):
                if len(steps) <= len(f["steps"]) or f["n"] < 2:
                    f["steps"] = steps
                f.update(n=f["n"] + 1, last=now)
                break
        else:
            sk["flows"].append({"goal": clean(goal, 120), "steps": steps, "n": 1, "last": now})
        sk["flows"] = sorted(sk["flows"], key=lambda f: (-f["n"], -f["last"]))[:MAX_FLOWS]
    # pitfalls: add what failed, forget what a successful run got past
    pit = {p["key"]: p for p in sk["pitfalls"]}
    for f in subj["failures"]:
        e = pit.setdefault(f["key"], {"key": f["key"], "text": f["text"], "n": 0})
        e["n"] += 1
        e["text"] = f["text"]
    if succeeded:
        for k in list(pit):
            if k in subj["worked"]:                       # the same step worked this time: it is no longer a pitfall
                del pit[k]
    sk["pitfalls"] = sorted(pit.values(), key=lambda p: -p["n"])[:MAX_PITFALLS]
    sk["version"] += 1
    sk["updated_at"] = now
    return sk


def learn(rows: list[dict], goal: str, succeeded: bool, load, save) -> list[str]:
    """Update the skill of every app/site this task touched. `load(key)` / `save(key, skill)` talk to the store."""
    touched = []
    for sid, subj in analyse(rows).items():
        if not (subj["steps"] or subj["failures"] or subj["open"]):
            continue
        key = sid
        save(key, update_skill(load(key), subj, goal, succeeded))
        touched.append(key)
    return touched


def render(sk: dict, limit: int = 1500) -> str:
    runs = sk.get("runs", {})
    lines = [f"[Learned skill: {sk.get('title')} ({sk.get('kind')}), from {runs.get('ok', 0) + runs.get('fail', 0)} earlier runs, "
             f"{runs.get('ok', 0)} worked] These are hints from past runs: verify each step, the app may have changed."]
    if sk.get("open"):
        lines.append(f"Open it with: {sk['open']}")
    if sk.get("controls"):
        by_win: dict[str, list[str]] = {}
        for c in sk["controls"][:14]:
            by_win.setdefault(c["window"], []).append(" ".join(x for x in (c["role"], f'"{c["name"]}"' if c["name"] else "",
                                                                          f'id={c["id"]}' if c["id"] else "") if x))
        for w, items in by_win.items():
            lines.append(f'Controls that worked{f" in window {w!r}" if w else ""}: ' + "; ".join(items))
    for f in sk.get("flows", [])[:2]:
        lines.append(f'Worked for "{f["goal"][:80]}": ' + " -> ".join(f["steps"]))
    if sk.get("pitfalls"):
        lines.append("Watch out: " + " | ".join(p["text"] for p in sk["pitfalls"][:3]))
    return "\n".join(lines)[:limit]


def matching(goal: str, skills: list[dict], limit: int = 2) -> list[dict]:
    """Skills whose app or site is named in the goal."""
    g = goal.lower()
    hits = []
    for sk in skills:
        words = {sk["title"].lower(), sk["key"].split(".")[0]}
        if any(w and len(w) >= 3 and re.search(rf"\b{re.escape(w)}\b", g) for w in words):
            hits.append(sk)
    hits.sort(key=lambda s: -(s.get("runs", {}).get("ok", 0)))
    return hits[:limit]
