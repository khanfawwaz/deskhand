"""Everything a person is entitled to do with their own data: take a copy (export) and have it erased (delete),
plus the retention schedule that removes old history by itself.

The export is built here from the stores' ordinary public methods so SQLite and MongoDB produce the same document.
It never contains token hashes or anyone else's workspace. Deleting is implemented inside each store
(`delete_workspace_data`, `purge_older_than`) because it needs raw deletes.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("taskpilot.datarights")

DAY = 86400.0
EXPORT_LIMIT = 1_000_000          # rows: effectively "all of it"
CONFIRM_PHRASE = "delete my data"


def retention_days() -> tuple[int, int]:
    """(task history days, usage-count days). 0 means keep forever. Overridable from the environment."""
    def num(name: str, default: int) -> int:
        try:
            return max(0, int(os.environ.get(name, default)))
        except ValueError:
            return default
    return num("TASKPILOT_RETENTION_DAYS", 90), num("TASKPILOT_USAGE_RETENTION_DAYS", 400)


def build_export(store, wid: str, usage_rows: list[dict]) -> dict:
    """One JSON-able document with the workspace's own data, nothing else."""
    ws = store.get_workspace(wid)
    if not ws:
        return {}
    workspace = {k: ws.get(k) for k in ("id", "name", "created_at", "owner_email", "max_tasks_per_day", "disabled")}
    devices = [{k: v for k, v in d.items() if k not in ("token_hash", "workspace")} for d in store.list_devices(wid)]
    clients = [{k: v for k, v in c.items() if k not in ("token_hash", "workspace")} for c in store.list_clients(wid)]
    tasks = []
    for t in sorted(store.list_tasks(limit=EXPORT_LIMIT, workspace=wid), key=lambda x: x.get("created_at", 0)):
        t = dict(t)
        t["transitions"] = [{k: v for k, v in tr.items() if k != "task_id"} for tr in store.transitions(t["id"])]
        t["actions"] = store.list_actions(t["id"])
        tasks.append(t)
    return {
        "exported_at": time.time(),
        "about": "Your Deskhand data: your workspace, computers, signed-in devices, tasks (with every step), "
                 "learned notes, saved preferences and model-usage counts. Sign-in tokens are never included.",
        "workspace": workspace,
        "devices": devices,
        "controllers": clients,
        "tasks": tasks,
        "experiences": store.list_experiences(limit=EXPORT_LIMIT, workspace=wid),
        "skills": store.list_skills(wid),
        "preferences": store.list_preferences(wid, limit=EXPORT_LIMIT),
        "usage": usage_rows,
    }


async def retention_loop(store, every_s: float = DAY) -> None:
    """Run the retention schedule now, then once a day, for as long as the server runs."""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(purge, store)
        except Exception:                                   # a failed clean-up must never take the server down
            log.exception("retention clean-up failed; will try again next time")
        await asyncio.sleep(every_s)


def purge(store) -> dict:
    """Apply the retention schedule once. Returns what was removed (empty dict when retention is off)."""
    task_days, usage_days = retention_days()
    now = time.time()
    task_cut = now - task_days * DAY if task_days else 0
    usage_cut = now - usage_days * DAY if usage_days else 0
    if not task_cut and not usage_cut:
        return {}
    gone = store.purge_older_than(task_cut, usage_cut)
    if any(gone.values()):
        log.info("retention: removed %s", gone)
    return gone
