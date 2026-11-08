"""MongoDB backend with the same public interface as SqliteStore.

Document model (the point of using Mongo here):
  tasks    _id=task id; the state-change history is EMBEDDED as `transitions: [...]`,
           so a state change and its history entry are one atomic single-document write.
  actions  _id=action_id; `params` and `result` are real sub-documents, not JSON strings.
  devices  _id=device_id; only the SHA-256 of the token is stored.
  metrics  one doc per measurement; a TTL index expires them after 30 days.

Uses the synchronous PyMongo driver: calls are millisecond-scale on a local mongod, and it keeps
the orchestrator code identical for both backends.
"""
from __future__ import annotations

import hmac
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .states import ALLOWED, TERMINAL, InvalidTransition, TaskState
from .store import DEFAULT_WS, _hash, scrub_params

METRIC_TTL_S = 30 * 24 * 3600


def _out(doc: dict | None, id_field: str) -> dict | None:
    """Mongo doc -> the dict shape SqliteStore returns (`_id` becomes the named id field)."""
    if doc is None:
        return None
    d = dict(doc)
    d[id_field] = d.pop("_id")
    return d


class MongoStore:
    def __init__(self, uri: str, db_name: str = "taskpilot", debug_logging: bool = False,
                 client: Any = None):
        if client is None:
            from pymongo import MongoClient
            client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
            client.admin.command("ping")          # fail fast if the server is unreachable
        self._client = client
        self._db = client[db_name]
        self.debug_logging = debug_logging
        self._last_created = 0.0
        self._db.clients.create_index("token_hash")
        self._db.tasks.create_index("created_at")
        self._db.actions.create_index([("task_id", 1), ("seq", 1)])
        self._db.metrics.create_index("name")
        self._db.tasks.create_index("workspace")
        for coll in ("tasks", "devices", "clients", "experiences"):      # databases made before workspaces existed
            self._db[coll].update_many({"workspace": {"$exists": False}}, {"$set": {"workspace": DEFAULT_WS}})
        if self._db.workspaces.count_documents({"_id": DEFAULT_WS}, limit=1) == 0:
            self._db.workspaces.insert_one({"_id": DEFAULT_WS, "name": "Main", "created_at": time.time(),
                                            "max_tasks_per_day": 100, "disabled": False})
        self._db.metrics.create_index("at_dt", expireAfterSeconds=METRIC_TTL_S)

    def _created_at(self) -> float:
        """Strictly increasing, so 'newest first' is stable even on coarse clocks (Windows ~15ms)."""
        now = time.time()
        if now <= self._last_created:
            now = self._last_created + 1e-6
        self._last_created = now
        return now

    def close(self) -> None:
        self._client.close()

    # -------------------------------------------------------------- devices
    def enroll_device(self, device_id: str, name: str = "", workspace: str = DEFAULT_WS) -> str:
        from pymongo.errors import DuplicateKeyError
        token = secrets.token_urlsafe(32)
        try:
            self._db.devices.insert_one({"_id": device_id, "token_hash": _hash(token), "name": name,
                                         "enrolled_at": time.time(), "revoked": False,
                                         "last_seen": None, "capabilities": None, "workspace": workspace})
        except DuplicateKeyError:
            raise ValueError(f"device '{device_id}' already enrolled") from None
        return token

    def authenticate_device(self, device_id: str, token: str) -> bool:
        d = self._db.devices.find_one({"_id": device_id})
        if not d or d["revoked"]:
            return False
        return hmac.compare_digest(d["token_hash"], _hash(token))

    def revoke_device(self, device_id: str) -> bool:
        return self._db.devices.update_one({"_id": device_id}, {"$set": {"revoked": True}}).matched_count > 0

    def touch_device(self, device_id: str, capabilities: list[str] | None = None) -> None:
        upd: dict = {"last_seen": time.time()}
        if capabilities is not None:
            upd["capabilities"] = capabilities
        self._db.devices.update_one({"_id": device_id}, {"$set": upd})

    def device_known(self, device_id: str) -> bool:
        d = self._db.devices.find_one({"_id": device_id}, {"revoked": 1})
        return bool(d) and not d["revoked"]

    def device_exists(self, device_id: str) -> bool:
        return self._db.devices.count_documents({"_id": device_id}, limit=1) > 0

    def get_device(self, device_id: str) -> dict | None:
        return _out(self._db.devices.find_one({"_id": device_id}), "device_id")

    def list_devices(self, workspace: str | None = None) -> list[dict]:
        q = {"workspace": workspace} if workspace else {}
        return [_out(d, "device_id") | {"token_hash": None} for d in self._db.devices.find(q)]

    # ---------------------------------------------------------------- tasks
    def create_task(self, device_id: str, goal: str, max_actions: int = 25,
                    timeout_s: float = 300, permission_context: dict | None = None) -> str:
        tid, now = uuid.uuid4().hex[:12], self._created_at()
        dev = self._db.devices.find_one({"_id": device_id}, {"workspace": 1}) or {}
        self._db.tasks.insert_one({
            "workspace": dev.get("workspace", DEFAULT_WS),
            "_id": tid, "device_id": device_id, "goal": goal, "status": TaskState.QUEUED.value,
            "created_at": now, "updated_at": now, "max_actions": max_actions, "timeout_s": timeout_s,
            "permission_context": permission_context or {}, "result": None, "error": None,
            "transitions": [{"from_state": None, "to_state": TaskState.QUEUED.value, "at": now,
                             "note": "created"}]})
        return tid

    def _task(self, tid: str) -> dict | None:
        return self._db.tasks.find_one({"_id": tid})

    def get_task(self, tid: str) -> dict | None:
        d = self._task(tid)
        if d is None:
            return None
        d = _out(d, "id")
        d.pop("transitions", None)           # same shape as SqliteStore.get_task
        return d

    def list_tasks(self, limit: int = 50, workspace: str | None = None) -> list[dict]:
        q = {"workspace": workspace} if workspace else {}
        out = []
        for d in self._db.tasks.find(q, {"transitions": 0}).sort("created_at", -1).limit(limit):
            out.append(_out(d, "id"))
        return out

    def count_tasks_since(self, workspace: str, since: float) -> int:
        return self._db.tasks.count_documents({"workspace": workspace, "created_at": {"$gte": since}})

    def active_task_ids(self, workspace: str) -> list[str]:
        done = [t.value for t in TERMINAL]
        return [d["_id"] for d in self._db.tasks.find({"workspace": workspace, "status": {"$nin": done}}, {"_id": 1})]

    def transition(self, tid: str, new: TaskState, note: str = "",
                   result: str | None = None, error: str | None = None) -> bool:
        """Guarded + atomic: the update only applies if the status is still what we validated
        against (optimistic concurrency), so two writers can never both win."""
        for _ in range(5):
            d = self._db.tasks.find_one({"_id": tid}, {"status": 1})
            cur = TaskState(d["status"])
            if cur in TERMINAL:
                return False
            if cur == new:
                return True
            if new not in ALLOWED[cur]:
                raise InvalidTransition(f"{cur.value} -> {new.value} is not allowed")
            now = time.time()
            sets: dict = {"status": new.value, "updated_at": now}
            if result is not None:
                sets["result"] = result
            if error is not None:
                sets["error"] = error
            r = self._db.tasks.update_one(
                {"_id": tid, "status": cur.value},
                {"$set": sets, "$push": {"transitions": {
                    "from_state": cur.value, "to_state": new.value, "at": now, "note": note}}})
            if r.modified_count == 1:
                return True
        raise RuntimeError("transition contention: task state kept changing")

    def transitions(self, tid: str) -> list[dict]:
        d = self._db.tasks.find_one({"_id": tid}, {"transitions": 1})
        return [t | {"task_id": tid, "id": i + 1} for i, t in enumerate(d["transitions"])] if d else []

    # -------------------------------------------------------------- actions
    def _scrub(self, params: dict) -> dict:
        if self.debug_logging:
            return params
        return scrub_params(params)

    def record_action(self, action_id: str, task_id: str, seq: int, tool: str, params: dict,
                      status: str, decision: str = "", model_latency_ms: float | None = None) -> None:
        self._db.actions.insert_one({
            "_id": action_id, "task_id": task_id, "seq": seq, "tool": tool,
            "params": self._scrub(params), "status": status, "decision": decision,
            "proposed_at": time.time(), "dispatched_at": None, "finished_at": None,
            "duration_ms": None, "model_latency_ms": model_latency_ms, "retries": 0,
            "verification": None, "error": None, "result": None})

    def update_action(self, action_id: str, **fields: Any) -> None:
        allowed = {"status", "dispatched_at", "finished_at", "duration_ms", "retries",
                   "verification", "error", "result", "decision"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            self._db.actions.update_one({"_id": action_id}, {"$set": sets})

    def get_action(self, action_id: str) -> dict | None:
        return _out(self._db.actions.find_one({"_id": action_id}), "action_id")

    def list_actions(self, tid: str) -> list[dict]:
        return [_out(d, "action_id") for d in
                self._db.actions.find({"task_id": tid}).sort([("seq", 1), ("proposed_at", 1)])]

    # -------------------------------------------------------------- experience memory
    def add_experience(self, exp: dict, workspace: str = DEFAULT_WS) -> str:
        eid = uuid.uuid4().hex[:12]
        self._db.experiences.insert_one({"_id": eid, **exp, "workspace": workspace})
        return eid

    def list_experiences(self, limit: int = 300, workspace: str | None = None) -> list[dict]:
        q = {"workspace": workspace} if workspace else {}
        return [{k: v for k, v in d.items() if k not in ("_id", "workspace")} | {"id": d["_id"]}
                for d in self._db.experiences.find(q).sort("created_at", -1).limit(limit)]

    # -------------------------------------------------------------- paired controller apps ("clients")
    def create_client(self, name: str, workspace: str = DEFAULT_WS) -> tuple[str, str]:
        cid, token = uuid.uuid4().hex[:10], secrets.token_urlsafe(32)
        self._db.clients.insert_one({"_id": cid, "name": name[:60], "token_hash": _hash(token),
                                     "created_at": time.time(), "revoked": False, "last_seen": None,
                                     "workspace": workspace})
        return cid, token

    def authenticate_client(self, token: str) -> dict | None:
        d = self._db.clients.find_one({"token_hash": _hash(token), "revoked": False})
        if not d:
            return None
        now = time.time()
        self._db.clients.update_one({"_id": d["_id"]}, {"$set": {"last_seen": now}})
        out = _out(d, "client_id")
        out.pop("token_hash")
        out["last_seen"] = now
        return out

    def list_clients(self, workspace: str | None = None) -> list[dict]:
        q = {"workspace": workspace} if workspace else {}
        return [{k: v for k, v in _out(d, "client_id").items() if k != "token_hash"}
                for d in self._db.clients.find(q).sort("created_at", 1)]

    def revoke_client(self, client_id: str, workspace: str | None = None) -> bool:
        q: dict = {"_id": client_id}
        if workspace:
            q["workspace"] = workspace
        return self._db.clients.update_one(q, {"$set": {"revoked": True}}).matched_count > 0

    # -------------------------------------------------------------- workspaces
    def create_workspace(self, name: str, max_tasks_per_day: int = 100, owner_email: str = "") -> str:
        wid = "w_" + uuid.uuid4().hex[:10]
        self._db.workspaces.insert_one({"_id": wid, "name": name[:60], "created_at": time.time(),
                                        "max_tasks_per_day": max_tasks_per_day, "disabled": False,
                                        "owner_email": owner_email})
        return wid

    def find_workspace_by_email(self, owner_email: str) -> dict | None:
        if not owner_email:
            return None
        return _out(self._db.workspaces.find_one({"owner_email": owner_email}, sort=[("created_at", 1)]), "id")

    def get_workspace(self, wid: str) -> dict | None:
        return _out(self._db.workspaces.find_one({"_id": wid}), "id")

    def list_workspaces(self) -> list[dict]:
        return [_out(d, "id") for d in self._db.workspaces.find().sort("created_at", 1)]

    def update_workspace(self, wid: str, **fields: Any) -> bool:
        allowed = {"name": str, "max_tasks_per_day": int, "disabled": bool}
        sets = {k: allowed[k](v) for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        return self._db.workspaces.update_one({"_id": wid}, {"$set": sets}).matched_count > 0

    # -------------------------------------------------------------- recipes
    def save_recipe(self, recipe: dict) -> None:
        now = time.time()
        doc = {k: v for k, v in recipe.items() if k not in ("name", "created_at", "updated_at")}
        doc.setdefault("status", "draft")
        self._db.recipes.update_one({"_id": recipe["name"]},
                                    {"$set": {**doc, "updated_at": now}, "$setOnInsert": {"created_at": now}},
                                    upsert=True)

    def get_recipe(self, name: str) -> dict | None:
        return _out(self._db.recipes.find_one({"_id": name}), "name")

    def list_recipes(self, status: str | None = None) -> list[dict]:
        q = {"status": status} if status else {}
        return [_out(d, "name") for d in self._db.recipes.find(q).sort("_id", 1)]

    def set_recipe_status(self, name: str, status: str) -> bool:
        return self._db.recipes.update_one({"_id": name}, {"$set": {"status": status, "updated_at": time.time()}}
                                           ).matched_count > 0

    def delete_recipe(self, name: str) -> bool:
        return self._db.recipes.delete_one({"_id": name}).deleted_count > 0

    # -------------------------------------------------------------- metrics
    def add_metric(self, task_id: str | None, name: str, value_ms: float) -> None:
        now = time.time()
        self._db.metrics.insert_one({"task_id": task_id, "name": name, "value_ms": value_ms,
                                     "at": now, "at_dt": datetime.fromtimestamp(now, timezone.utc)})

    def metrics(self, name: str | None = None) -> list[dict]:
        return [{k: v for k, v in d.items() if k not in ("_id", "at_dt")}
                for d in self._db.metrics.find({"name": name} if name else {})]
