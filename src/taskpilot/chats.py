"""A chat is a thread of tasks: each follow-up message is a new task that says which task it continues
(`permission_context["continue_from"]`). Everything here works on the stores' ordinary public methods, so SQLite and
MongoDB behave the same. A task whose parent was deleted simply becomes the start of its own chat.
"""
from __future__ import annotations

import json
from typing import Any

from .states import TERMINAL

TURN_CHARS = 400            # how much of an earlier answer the model is shown
MAX_TURNS = 4               # how many earlier turns the model is shown
MAX_WALK = 60               # a chat can be long; never loop forever on bad data
_LIMIT = 1_000_000


def ctx_of(task: dict | None) -> dict:
    """The task's permission_context as a dict (SQLite keeps JSON text, MongoDB keeps a document)."""
    raw = (task or {}).get("permission_context")
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def continue_of(task: dict | None) -> str | None:
    cf = ctx_of(task).get("continue_from")
    return cf if isinstance(cf, str) and cf else None


def thread_root(store, task: dict) -> str:
    """The id of the first task of the chat this task belongs to."""
    cur, seen = task, set()
    for _ in range(MAX_WALK):
        parent_id = continue_of(cur)
        if not parent_id or parent_id in seen:
            break
        seen.add(cur["id"])
        parent = store.get_task(parent_id)
        if parent is None or (parent.get("workspace") or "main") != (task.get("workspace") or "main"):
            break
        cur = parent
    return cur["id"]


def thread_ids(store, workspace: str, tid: str) -> list[str]:
    """Every task id in the same chat as `tid`, oldest first (empty if `tid` is not in this workspace)."""
    tasks = {t["id"]: t for t in store.list_tasks(limit=_LIMIT, workspace=workspace)}
    if tid not in tasks:
        return []

    def root(t: dict) -> str:
        cur, seen = t, set()
        for _ in range(MAX_WALK):
            p = continue_of(cur)
            if not p or p in seen or p not in tasks:
                break
            seen.add(cur["id"])
            cur = tasks[p]
        return cur["id"]

    want = root(tasks[tid])
    members = [t for t in tasks.values() if root(t) == want]
    return [t["id"] for t in sorted(members, key=lambda t: t.get("created_at", 0))]


def finished_chats(store, workspace: str) -> list[list[str]]:
    """All chats of a workspace in which every task is finished (running chats are never listed)."""
    tasks = {t["id"]: t for t in store.list_tasks(limit=_LIMIT, workspace=workspace)}
    done = {t.value for t in TERMINAL}

    def root(t: dict) -> str:
        cur, seen = t, set()
        for _ in range(MAX_WALK):
            p = continue_of(cur)
            if not p or p in seen or p not in tasks:
                break
            seen.add(cur["id"])
            cur = tasks[p]
        return cur["id"]

    groups: dict[str, list[dict]] = {}
    for t in tasks.values():
        groups.setdefault(root(t), []).append(t)
    return [[t["id"] for t in sorted(g, key=lambda t: t.get("created_at", 0))]
            for g in groups.values() if all(t["status"] in done for t in g)]


def _clip(s: Any, n: int = TURN_CHARS) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def history_block(store, task: dict) -> str:
    """'Earlier in this conversation' for the model: what was asked and how it ended, oldest first ('' for a first message)."""
    turns, cur, seen = [], task, {task["id"]}
    for _ in range(MAX_TURNS):
        pid = continue_of(cur)
        if not pid or pid in seen:
            break
        parent = store.get_task(pid)
        if parent is None or (parent.get("workspace") or "main") != (task.get("workspace") or "main"):
            break
        seen.add(pid)
        turns.append(parent)
        cur = parent
    if not turns:
        return ""
    lines = ["Earlier in this conversation (oldest first). The new request may refer back to it, for example "
             "'continue', 'try again' or 'now close it':"]
    for i, t in enumerate(reversed(turns), 1):
        outcome = t.get("result") if t.get("status") == "completed" else (t.get("error") or t.get("result"))
        lines.append(f"{i}. Asked: {_clip(t.get('goal'))}")
        lines.append(f"   Outcome ({t.get('status')}): {_clip(outcome) or 'no details'}")
    return "\n".join(lines)
