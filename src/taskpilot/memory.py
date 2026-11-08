"""Experience memory: what worked last time for a similar goal.

After a task is verified done, Pilot keeps a short, NON-SENSITIVE summary of the steps that worked (tools, pages,
control names; never typed text, file contents or answers). The next similar goal gets it as a hint, so Pilot starts
from experience instead of rediscovering each site. Hints say 'verify each step': pages and names change.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from .grounding import STOP
from .hygiene import line, safe

SKIP_TOOLS = {"capture_screen", "invalid", "done", "windows.list", "get_active_window"}


def _short(s: object, n: int = 60) -> str:
    s = line(s, n)                                   # one line, harmless characters
    return s if safe(s) else "(name withheld)"      # never store text that looks like an instruction or a secret


def summarize_step(row: dict) -> str | None:
    tool = row["tool"]
    if tool in SKIP_TOOLS or row.get("status") not in ("succeeded",):
        return None
    p = row.get("params") or {}
    a = p.get("args", p) if isinstance(p, dict) else {}
    if tool in ("browser.open_url", "browser.open_private"):
        u = urlparse(str(a.get("url", "")))
        return f"open {u.netloc}{_short(u.path, 50)}" + ("?…" if u.query else "")
    if tool == "ui.click":
        return f'click "{_short(a.get("name") or a.get("id"))}"'
    if tool == "ui.read":
        return f'read "{_short(a.get("name") or a.get("id"))}"'
    if tool == "ui.type":
        return f'type into "{_short(a.get("name") or a.get("id"))}"'          # never the typed text
    if tool == "ui.snapshot":
        return f'look at the page (query "{_short(a.get("query"), 30)}")' if a.get("query") else "look at the page"
    if tool == "ui.read_page":
        return "read the page until it stops changing"
    if tool == "windows.focus":
        return f'bring window "{_short(a.get("title"))}" forward'
    if tool == "launch_application":
        return f"launch {_short(a.get('app_id'), 30)}"
    if tool == "press_key":
        return f"press {_short(a.get('key'), 20)}"
    if tool == "hotkey":
        return "press " + "+".join(map(str, a.get("keys", [])))
    return tool


def summarize_run(goal: str, rows: list[dict]) -> dict | None:
    steps: list[str] = []
    for r in rows:
        s = summarize_step(r)
        if s and (not steps or steps[-1] != s):
            steps.append(s)
    if not steps:
        return None
    return {"goal": goal[:300], "steps": steps[:20], "created_at": time.time()}


def tokens(goal: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", goal.lower()) if len(t) >= 4 and t not in STOP}


def hints_for(goal: str, experiences: list[dict], limit: int = 2, min_sim: float = 0.45) -> str:
    g = tokens(goal)
    if not g:
        return ""
    scored = []
    for e in experiences:
        t = tokens(e.get("goal", ""))
        if not t:
            continue
        sim = len(g & t) / len(g | t)
        if sim >= min_sim:
            scored.append((sim, -len(e.get("steps", [])), e))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    for _, _, e in scored[:limit]:
        path = " -> ".join(e["steps"])
        out.append(f'[Experience] A similar task ("{_short(e["goal"], 70)}") worked like this: {path}. '
                   "Reuse the approach, but verify every step: pages and names may have changed.")
    return "\n".join(out)
