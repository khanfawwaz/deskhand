"""SQLite persistence: tasks, transitions, actions, devices.

State is written before dispatch so a crash leaves an auditable 'dispatched' row.
Screenshots and typed text are never stored unless `debug_logging` is enabled.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any

from .states import TERMINAL, TaskState, check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, goal TEXT NOT NULL,
  status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  max_actions INTEGER NOT NULL, timeout_s REAL NOT NULL, permission_context TEXT NOT NULL,
  result TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS transitions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
  from_state TEXT, to_state TEXT NOT NULL, at REAL NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS actions(
  action_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, seq INTEGER NOT NULL,
  tool TEXT NOT NULL, params TEXT NOT NULL, status TEXT NOT NULL,
  decision TEXT, proposed_at REAL NOT NULL, dispatched_at REAL, finished_at REAL,
  duration_ms REAL, model_latency_ms REAL, retries INTEGER NOT NULL DEFAULT 0,
  verification TEXT, error TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS devices(
  device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, name TEXT,
  enrolled_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
  last_seen REAL, capabilities TEXT);
CREATE TABLE IF NOT EXISTS recipes(
  name TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS clients(
  client_id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL, created_at REAL NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0, last_seen REAL);
CREATE INDEX IF NOT EXISTS clients_token ON clients(token_hash);
CREATE TABLE IF NOT EXISTS experiences(
  id TEXT PRIMARY KEY, goal TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL,
  max_tasks_per_day INTEGER NOT NULL DEFAULT 100, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS skills(
  workspace TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY(workspace, key));
CREATE TABLE IF NOT EXISTS preferences(
  id TEXT PRIMARY KEY, workspace TEXT NOT NULL DEFAULT 'main', text TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS preferences_ws ON preferences(workspace);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, workspace TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '', prompt INTEGER NOT NULL DEFAULT 0, completion INTEGER NOT NULL DEFAULT 0,
  latency_ms REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ok', cost REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_ws_ts ON usage(workspace, ts);
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, name TEXT NOT NULL,
  value_ms REAL NOT NULL, at REAL NOT NULL);
"""

SENSITIVE_PARAMS = {"text", "content"}
DEFAULT_WS = "main"                       # the original single-owner space; existing data lives here
WS_TABLES = ("tasks", "devices", "clients", "experiences")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def scrub_params(params: dict) -> dict:
    """Redact typed text / file content, including inside a registry tool's nested `args`."""
    out = {k: ("<redacted>" if k in SENSITIVE_PARAMS else v) for k, v in params.items()}
    if isinstance(out.get("args"), dict):
        out["args"] = {k: ("<redacted>" if k in SENSITIVE_PARAMS else v) for k, v in out["args"].items()}
    return out


def _loads(v: Any) -> Any:
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return v


def _action(d: dict) -> dict:
    d["params"], d["result"] = _loads(d.get("params")), _loads(d.get("result"))
    return d


def _device(d: dict) -> dict:
    d["capabilities"] = _loads(d.get("capabilities"))
    d["revoked"] = bool(d.get("revoked"))
    return d


class SqliteStore:
    def __init__(self, path: str = ":memory:", debug_logging: bool = False):
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")           # readers do not wait for writers; survives a crash cleanly
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=15000")
        self._lock = threading.RLock()
        self.debug_logging = debug_logging
        self._last_created = 0.0
        with self._lock:
            self._db.executescript(SCHEMA)
            for t in WS_TABLES:                                   # upgrade databases made before workspaces existed
                cols = [r["name"] for r in self._db.execute(f"PRAGMA table_info({t})")]
                if "workspace" not in cols:
                    self._db.execute(f"ALTER TABLE {t} ADD COLUMN workspace TEXT NOT NULL DEFAULT '{DEFAULT_WS}'")
            if "owner_email" not in [r["name"] for r in self._db.execute("PRAGMA table_info(workspaces)")]:
                self._db.execute("ALTER TABLE workspaces ADD COLUMN owner_email TEXT NOT NULL DEFAULT ''")
            self._db.execute("INSERT OR IGNORE INTO workspaces(id,name,created_at) VALUES(?,?,?)",
                             (DEFAULT_WS, "Main", time.time()))
            self._db.commit()

    def _created_at(self) -> float:
        """Strictly increasing, so 'newest first' is stable even on coarse clocks (Windows ~15ms)."""
        now = time.time()
        if now <= self._last_created:
            now = self._last_created + 1e-6
        self._last_created = now
        return now

    def _q(self, sql: str, *a: Any) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, a)
            self._db.commit()
            return cur.fetchall()

    # -------------------------------------------------------------- devices
    def enroll_device(self, device_id: str, name: str = "", workspace: str = DEFAULT_WS) -> str:
        """Returns the per-device token ONCE; only its hash is stored."""
        token = secrets.token_urlsafe(32)
        self._q("INSERT INTO devices(device_id,token_hash,name,enrolled_at,workspace) VALUES(?,?,?,?,?)",
                device_id, _hash(token), name, time.time(), workspace)
        return token

    def authenticate_device(self, device_id: str, token: str) -> bool:
        rows = self._q("SELECT token_hash,revoked FROM devices WHERE device_id=?", device_id)
        if not rows or rows[0]["revoked"]:
            return False
        return hmac.compare_digest(rows[0]["token_hash"], _hash(token))

    def revoke_device(self, device_id: str) -> bool:
        self._q("UPDATE devices SET revoked=1 WHERE device_id=?", device_id)
        return bool(self._q("SELECT 1 FROM devices WHERE device_id=?", device_id))

    def touch_device(self, device_id: str, capabilities: list[str] | None = None) -> None:
        self._q("UPDATE devices SET last_seen=?, capabilities=COALESCE(?,capabilities) "
                "WHERE device_id=?", time.time(),
                json.dumps(capabilities) if capabilities is not None else None, device_id)

    def device_known(self, device_id: str) -> bool:
        r = self._q("SELECT revoked FROM devices WHERE device_id=?", device_id)
        return bool(r) and not r[0]["revoked"]

    def device_exists(self, device_id: str) -> bool:
        return bool(self._q("SELECT 1 FROM devices WHERE device_id=?", device_id))

    def get_device(self, device_id: str) -> dict | None:
        """Internal: includes token_hash. Never return this from an API."""
        r = self._q("SELECT * FROM devices WHERE device_id=?", device_id)
        return _device(dict(r[0])) if r else None

    def list_devices(self, workspace: str | None = None) -> list[dict]:
        rows = (self._q("SELECT * FROM devices WHERE workspace=?", workspace) if workspace
                else self._q("SELECT * FROM devices"))
        return [_device(dict(r)) | {"token_hash": None} for r in rows]

    # ---------------------------------------------------------------- tasks
    def create_task(self, device_id: str, goal: str, max_actions: int = 25,
                    timeout_s: float = 300, permission_context: dict | None = None) -> str:
        tid = uuid.uuid4().hex[:12]
        now = self._created_at()
        dev = self.get_device(device_id)
        self._q("INSERT INTO tasks(id,device_id,goal,status,created_at,updated_at,max_actions,timeout_s,"
                "permission_context,workspace) VALUES(?,?,?,?,?,?,?,?,?,?)", tid, device_id, goal,
                TaskState.QUEUED.value, now, now, max_actions, timeout_s,
                json.dumps(permission_context or {}), (dev or {}).get("workspace", DEFAULT_WS))
        self._q("INSERT INTO transitions(task_id,from_state,to_state,at,note) VALUES(?,?,?,?,?)",
                tid, None, TaskState.QUEUED.value, now, "created")
        return tid

    def get_task(self, tid: str) -> dict | None:
        r = self._q("SELECT * FROM tasks WHERE id=?", tid)
        return dict(r[0]) if r else None

    def list_tasks(self, limit: int = 50, workspace: str | None = None) -> list[dict]:
        if workspace:
            return [dict(r) for r in self._q(
                "SELECT * FROM tasks WHERE workspace=? ORDER BY created_at DESC LIMIT ?", workspace, limit)]
        return [dict(r) for r in self._q("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", limit)]

    def count_tasks_since(self, workspace: str, since: float) -> int:
        return self._q("SELECT COUNT(*) AS n FROM tasks WHERE workspace=? AND created_at>=?",
                       workspace, since)[0]["n"]

    # ---------------------------------------------------------------- usage (every model call)
    def add_usage(self, rec: dict) -> None:
        self._q("INSERT INTO usage(ts,workspace,task_id,model,kind,prompt,completion,latency_ms,status,cost) VALUES(?,?,?,?,?,?,?,?,?,?)",
                rec["ts"], rec.get("workspace", ""), rec.get("task_id", ""), rec.get("model", ""), rec.get("kind", ""),
                int(rec.get("prompt", 0)), int(rec.get("completion", 0)), float(rec.get("latency_ms", 0)),
                rec.get("status", "ok"), float(rec.get("cost", 0)))

    def usage_since(self, since: float, workspace: str | None = None) -> dict:
        """Totals since a time, and a per-workspace breakdown (busiest first)."""
        where, args = ("ts>=?", [since]) if workspace is None else ("ts>=? AND workspace=?", [since, workspace])
        t = self._q(f"SELECT COUNT(*) AS calls, COALESCE(SUM(prompt),0) AS prompt, COALESCE(SUM(completion),0) AS completion, "
                    f"COALESCE(SUM(cost),0) AS cost, COALESCE(SUM(status!='ok'),0) AS errors FROM usage WHERE {where}", *args)[0]
        by = self._q(f"SELECT workspace, COUNT(*) AS calls, SUM(prompt) AS prompt, SUM(completion) AS completion, SUM(cost) AS cost "
                     f"FROM usage WHERE {where} GROUP BY workspace ORDER BY SUM(prompt)+SUM(completion) DESC LIMIT 200", *args)
        return {"calls": t["calls"], "prompt": t["prompt"], "completion": t["completion"], "cost": t["cost"], "errors": t["errors"],
                "by_workspace": [{"workspace": r["workspace"], "calls": r["calls"], "prompt": r["prompt"] or 0,
                                  "completion": r["completion"] or 0, "cost": r["cost"] or 0} for r in by]}

    def active_task_ids(self, workspace: str) -> list[str]:
        """Tasks of one workspace that have not finished (used to scope an emergency stop)."""
        done = [t.value for t in TERMINAL]
        marks = ",".join("?" * len(done))
        return [r["id"] for r in self._q(
            f"SELECT id FROM tasks WHERE workspace=? AND status NOT IN ({marks})", workspace, *done)]

    def transition(self, tid: str, new: TaskState, note: str = "",
                   result: str | None = None, error: str | None = None) -> bool:
        """Atomic guarded transition. Returns False if the task is already terminal."""
        with self._lock:
            row = self._db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
            cur = TaskState(row["status"])
            if cur in TERMINAL:
                return False
            if cur == new:
                return True
            check_transition(cur, new)
            now = time.time()
            self._db.execute(
                "UPDATE tasks SET status=?,updated_at=?,result=COALESCE(?,result),"
                "error=COALESCE(?,error) WHERE id=?", (new.value, now, result, error, tid))
            self._db.execute(
                "INSERT INTO transitions(task_id,from_state,to_state,at,note) VALUES(?,?,?,?,?)",
                (tid, cur.value, new.value, now, note))
            self._db.commit()
            return True

    def transitions(self, tid: str) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT * FROM transitions WHERE task_id=? ORDER BY id", tid)]

    # -------------------------------------------------------------- actions
    def _scrub(self, params: dict) -> dict:
        if self.debug_logging:
            return params
        return scrub_params(params)

    def record_action(self, action_id: str, task_id: str, seq: int, tool: str, params: dict,
                      status: str, decision: str = "", model_latency_ms: float | None = None) -> None:
        self._q("INSERT INTO actions(action_id,task_id,seq,tool,params,status,decision,"
                "proposed_at,model_latency_ms) VALUES(?,?,?,?,?,?,?,?,?)",
                action_id, task_id, seq, tool, json.dumps(self._scrub(params)), status,
                decision, time.time(), model_latency_ms)

    def update_action(self, action_id: str, **fields: Any) -> None:
        allowed = {"status", "dispatched_at", "finished_at", "duration_ms", "retries",
                   "verification", "error", "result", "decision"}
        sets = {k: (json.dumps(v, default=str) if k == "result" and not isinstance(v, str) else v)
                for k, v in fields.items() if k in allowed}
        if not sets:
            return
        self._q(f"UPDATE actions SET {','.join(k + '=?' for k in sets)} WHERE action_id=?",
                *sets.values(), action_id)

    def get_action(self, action_id: str) -> dict | None:
        r = self._q("SELECT * FROM actions WHERE action_id=?", action_id)
        return _action(dict(r[0])) if r else None

    def list_actions(self, tid: str) -> list[dict]:
        return [_action(dict(r)) for r in self._q(
            "SELECT * FROM actions WHERE task_id=? ORDER BY seq, proposed_at", tid)]

    # -------------------------------------------------------------- experience memory
    def add_experience(self, exp: dict, workspace: str = DEFAULT_WS) -> str:
        eid = uuid.uuid4().hex[:12]
        self._q("INSERT INTO experiences(id,goal,data,created_at,workspace) VALUES(?,?,?,?,?)",
                eid, exp["goal"], json.dumps(exp), exp.get("created_at", time.time()), workspace)
        return eid

    def list_experiences(self, limit: int = 300, workspace: str | None = None) -> list[dict]:
        rows = (self._q("SELECT * FROM experiences WHERE workspace=? ORDER BY created_at DESC LIMIT ?", workspace, limit)
                if workspace else self._q("SELECT * FROM experiences ORDER BY created_at DESC LIMIT ?", limit))
        return [json.loads(r["data"]) | {"id": r["id"]} for r in rows]

    # -------------------------------------------------------------- skills (what was learned about each app / site)
    def get_skill(self, workspace: str, key: str) -> dict | None:
        r = self._q("SELECT data FROM skills WHERE workspace=? AND key=?", workspace, key)
        return json.loads(r[0]["data"]) if r else None

    def save_skill(self, workspace: str, key: str, data: dict) -> None:
        self._q("INSERT INTO skills(workspace,key,data,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(workspace,key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                workspace, key, json.dumps(data), data.get("updated_at", time.time()))

    def list_skills(self, workspace: str) -> list[dict]:
        return [json.loads(r["data"]) | {"id": r["key"]} for r in
                self._q("SELECT key, data FROM skills WHERE workspace=? ORDER BY updated_at DESC", workspace)]

    def delete_skill(self, workspace: str, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM skills WHERE workspace=? AND key=?", (workspace, key))
            self._db.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------- preferences (things the person asked to be remembered)
    def add_preference(self, workspace: str, text: str) -> str:
        """Save one preference (the same sentence twice is kept once). The oldest go when there are too many."""
        from .prefs import MAX_PER_WORKSPACE
        text = text.strip()
        with self._lock:
            for r in self._db.execute("SELECT id,text FROM preferences WHERE workspace=?", (workspace,)):
                if r["text"].strip().lower() == text.lower():
                    return r["id"]
            pid = "p_" + uuid.uuid4().hex[:10]
            self._db.execute("INSERT INTO preferences(id,workspace,text,created_at) VALUES(?,?,?,?)",
                             (pid, workspace, text, self._created_at()))
            for r in self._db.execute("SELECT id FROM preferences WHERE workspace=? ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                                      (workspace, MAX_PER_WORKSPACE)).fetchall():
                self._db.execute("DELETE FROM preferences WHERE id=?", (r["id"],))
            self._db.commit()
            return pid

    def list_preferences(self, workspace: str, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id,text,created_at FROM preferences WHERE workspace=? "
                                         "ORDER BY created_at DESC LIMIT ?", workspace, limit)]

    def delete_preference(self, workspace: str, pid: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM preferences WHERE id=? AND workspace=?", (pid, workspace))
            self._db.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------- paired controller apps ("clients")
    def create_client(self, name: str, workspace: str = DEFAULT_WS) -> tuple[str, str]:
        """A phone/laptop running the Pilot app. Returns (client_id, token); only the token's hash is stored."""
        cid, token = uuid.uuid4().hex[:10], secrets.token_urlsafe(32)
        self._q("INSERT INTO clients(client_id,name,token_hash,created_at,workspace) VALUES(?,?,?,?,?)",
                cid, name[:60], _hash(token), time.time(), workspace)
        return cid, token

    def authenticate_client(self, token: str) -> dict | None:
        r = self._q("SELECT * FROM clients WHERE token_hash=? AND revoked=0", _hash(token))
        if not r:
            return None
        now = time.time()
        self._q("UPDATE clients SET last_seen=? WHERE client_id=?", now, r[0]["client_id"])
        d = dict(r[0])
        d.pop("token_hash")
        d["revoked"], d["last_seen"] = False, now
        return d

    def list_clients(self, workspace: str | None = None) -> list[dict]:
        rows = (self._q("SELECT * FROM clients WHERE workspace=? ORDER BY created_at", workspace) if workspace
                else self._q("SELECT * FROM clients ORDER BY created_at"))
        return [{k: v for k, v in dict(r).items() if k != "token_hash"} | {"revoked": bool(r["revoked"])}
                for r in rows]

    def revoke_client(self, client_id: str, workspace: str | None = None) -> bool:
        """With `workspace` set, only a controller of that workspace can be revoked (a tenant can't touch another's)."""
        with self._lock:
            if workspace:
                cur = self._db.execute("UPDATE clients SET revoked=1 WHERE client_id=? AND workspace=?",
                                       (client_id, workspace))
            else:
                cur = self._db.execute("UPDATE clients SET revoked=1 WHERE client_id=?", (client_id,))
            self._db.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------- workspaces (one per person / friend group)
    def create_workspace(self, name: str, max_tasks_per_day: int = 100, owner_email: str = "") -> str:
        wid = "w_" + uuid.uuid4().hex[:10]
        self._q("INSERT INTO workspaces(id,name,created_at,max_tasks_per_day,owner_email) VALUES(?,?,?,?,?)",
                wid, name[:60], time.time(), max_tasks_per_day, owner_email)
        return wid

    def find_workspace_by_email(self, owner_email: str) -> dict | None:
        """The workspace created by this (normalised) email address, if any."""
        r = self._q("SELECT * FROM workspaces WHERE owner_email=? AND owner_email<>'' ORDER BY created_at LIMIT 1",
                    owner_email)
        return self._ws(r[0]) if r else None

    @staticmethod
    def _ws(r) -> dict:
        return {**dict(r), "disabled": bool(r["disabled"])}

    def get_workspace(self, wid: str) -> dict | None:
        r = self._q("SELECT * FROM workspaces WHERE id=?", wid)
        return self._ws(r[0]) if r else None

    def list_workspaces(self) -> list[dict]:
        return [self._ws(r) for r in self._q("SELECT * FROM workspaces ORDER BY created_at")]

    def update_workspace(self, wid: str, **fields: Any) -> bool:
        allowed = {"name": str, "max_tasks_per_day": int, "disabled": int}
        sets = {k: allowed[k](v) for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        with self._lock:
            cur = self._db.execute(f"UPDATE workspaces SET {','.join(k + '=?' for k in sets)} WHERE id=?",
                                   (*sets.values(), wid))
            self._db.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------- recipes
    def save_recipe(self, recipe: dict) -> None:
        """Upsert by name. `status` and timestamps live in columns; everything else in a JSON document."""
        now = time.time()
        data = {k: v for k, v in recipe.items() if k not in ("name", "status", "created_at", "updated_at")}
        status = recipe.get("status", "draft")
        if self._q("SELECT 1 FROM recipes WHERE name=?", recipe["name"]):
            self._q("UPDATE recipes SET status=?, data=?, updated_at=? WHERE name=?",
                    status, json.dumps(data), now, recipe["name"])
        else:
            self._q("INSERT INTO recipes(name,status,data,created_at,updated_at) VALUES(?,?,?,?,?)",
                    recipe["name"], status, json.dumps(data), now, now)

    @staticmethod
    def _recipe_row(r) -> dict:
        return {**json.loads(r["data"]), "name": r["name"], "status": r["status"],
                "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def get_recipe(self, name: str) -> dict | None:
        r = self._q("SELECT * FROM recipes WHERE name=?", name)
        return self._recipe_row(r[0]) if r else None

    def list_recipes(self, status: str | None = None) -> list[dict]:
        rows = (self._q("SELECT * FROM recipes WHERE status=? ORDER BY name", status) if status
                else self._q("SELECT * FROM recipes ORDER BY name"))
        return [self._recipe_row(r) for r in rows]

    def set_recipe_status(self, name: str, status: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE recipes SET status=?, updated_at=? WHERE name=?",
                                   (status, time.time(), name))
            self._db.commit()
            return cur.rowcount > 0

    def delete_recipe(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM recipes WHERE name=?", (name,))
            self._db.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------- the person's own data: export, delete, retention
    def export_workspace(self, wid: str) -> dict:
        from .datarights import build_export
        usage = [dict(r) for r in self._q("SELECT ts,task_id,model,kind,prompt,completion,latency_ms,status,cost "
                                          "FROM usage WHERE workspace=? ORDER BY ts", wid)]
        return build_export(self, wid, usage)

    def _delete_task_rows(self, ids: list[str]) -> dict:
        """Children first. Done in slices so a big history never trips SQLite's variable limit."""
        n = {"actions": 0, "transitions": 0, "metrics": 0}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for table in n:
                n[table] += self._db.execute(f"DELETE FROM {table} WHERE task_id IN ({marks})", chunk).rowcount
        return n

    def delete_tasks(self, ids: list[str]) -> int:
        """Delete these tasks with their steps, state history and measurements (a chat the person deleted).
        Returns how many tasks were removed. Other tasks that pointed at them just start their own chat."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return 0
        with self._lock:
            self._delete_task_rows(ids)
            n = 0
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                n += self._db.execute(f"DELETE FROM tasks WHERE id IN ({','.join('?' * len(chunk))})", chunk).rowcount
            self._db.commit()
            return n

    def delete_workspace_data(self, wid: str) -> dict:
        """Erase everything belonging to a workspace, including its row (the default 'main' workspace keeps its row)."""
        with self._lock:
            ids = [r["id"] for r in self._db.execute("SELECT id FROM tasks WHERE workspace=?", (wid,))]
            counts = self._delete_task_rows(ids)
            for table in ("tasks", "devices", "clients", "experiences", "skills", "preferences", "usage"):
                counts[table] = self._db.execute(f"DELETE FROM {table} WHERE workspace=?", (wid,)).rowcount
            counts["workspace"] = 0 if wid == DEFAULT_WS else self._db.execute(
                "DELETE FROM workspaces WHERE id=?", (wid,)).rowcount
            self._db.commit()
            return counts

    def purge_older_than(self, task_cutoff: float, usage_cutoff: float) -> dict:
        """Retention. Finished tasks (with their steps and measurements) and learned experiences older than
        `task_cutoff`, usage counts older than `usage_cutoff`. A cutoff of 0 skips that part. Skills are kept."""
        done = [t.value for t in TERMINAL]
        with self._lock:
            counts = {"tasks": 0, "actions": 0, "transitions": 0, "metrics": 0, "experiences": 0, "usage": 0}
            if task_cutoff:
                ids = [r["id"] for r in self._db.execute(
                    f"SELECT id FROM tasks WHERE updated_at<? AND status IN ({','.join('?' * len(done))})",
                    (task_cutoff, *done))]
                counts.update(self._delete_task_rows(ids))
                for i in range(0, len(ids), 400):
                    chunk = ids[i:i + 400]
                    counts["tasks"] += self._db.execute(
                        f"DELETE FROM tasks WHERE id IN ({','.join('?' * len(chunk))})", chunk).rowcount
                counts["metrics"] += self._db.execute("DELETE FROM metrics WHERE at<?", (task_cutoff,)).rowcount
                counts["experiences"] = self._db.execute("DELETE FROM experiences WHERE created_at<?",
                                                         (task_cutoff,)).rowcount
            if usage_cutoff:
                counts["usage"] = self._db.execute("DELETE FROM usage WHERE ts<?", (usage_cutoff,)).rowcount
            self._db.commit()
            return counts

    def add_metric(self, task_id: str | None, name: str, value_ms: float) -> None:
        self._q("INSERT INTO metrics(task_id,name,value_ms,at) VALUES(?,?,?,?)",
                task_id, name, value_ms, time.time())

    def metrics(self, name: str | None = None) -> list[dict]:
        if name:
            return [dict(r) for r in self._q("SELECT * FROM metrics WHERE name=?", name)]
        return [dict(r) for r in self._q("SELECT * FROM metrics")]


Store = SqliteStore   # default / backwards-compatible name


def open_store(target: str = ":memory:", debug_logging: bool = False, mongo_db: str = "taskpilot"):
    """`mongodb://...` or `mongodb+srv://...` -> MongoStore, anything else is a SQLite path."""
    if target.startswith(("mongodb://", "mongodb+srv://")):
        from .store_mongo import MongoStore
        return MongoStore(target, mongo_db, debug_logging=debug_logging)
    return SqliteStore(target, debug_logging=debug_logging)
