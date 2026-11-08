"""Run one TaskSpec through the REAL Orchestrator (unaided), then grade the end state with independent verifiers."""
from __future__ import annotations

import asyncio
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ..agent.executor import Executor
from ..devices import DeviceConnection, DeviceHub
from ..orchestrator import Config, Orchestrator
from ..policy import Policy
from ..store import Store
from .spec import CheckResult, EvalContext, TaskSpec

PASS, FALSE_SUCCESS, FAIL, PARTIAL, INCONCLUSIVE = "PASS", "FALSE_SUCCESS", "FAIL", "PARTIAL", "INCONCLUSIVE"
COMPLETED_LABEL = "task reported completed"


class TimedModel:
    """Wraps a model runtime and records how long its calls take (separates model latency from everything else)."""
    def __init__(self, inner):
        self.inner, self.calls, self.seconds = inner, 0, 0.0

    def __getattr__(self, name): return getattr(self.inner, name)

    async def propose(self, ctx):
        t = time.time()
        try:
            return await self.inner.propose(ctx)
        finally:
            self.calls += 1
            self.seconds += time.time() - t


class Loopback(DeviceConnection):
    def __init__(self, ex: Executor): self.ex = ex
    async def execute(self, req, timeout): return await self.ex.handle(req)
    async def query(self, action_id, timeout=5.0): return self.ex.lookup(action_id)
    async def cancel(self, task_id): self.ex.cancel_task(task_id)
    async def estop(self): self.ex.estop()


def classify_failure(status: str, error: str, actions: list[dict]) -> str:
    """Why did a run not succeed? A small fixed taxonomy so reports can be compared and trended."""
    e = (error or "").lower()
    if status == "completed":
        return ""
    if "model runtime error" in e or "model api error" in e:
        return "MODEL_API"
    if "no progress" in e or "not making progress" in e:
        return "LOOP"
    if "action limit" in e or "max_actions" in e or "too many actions" in e:
        return "ACTION_LIMIT"
    if "timed out" in e or "timeout" in e:
        return "TIMEOUT"
    if "completion not verifiable" in e or "evidence" in e:
        return "UNVERIFIED_DONE"
    if "device" in e or "unavailable" in e:
        return "DEVICE"
    if "invalid" in e or "schema" in e or "format" in e:
        return "MODEL_FORMAT"
    if "not found" in e or "no element" in e or "no window" in e:
        return "NOT_FOUND"
    if "ambiguous" in e or "matches" in e:
        return "AMBIGUOUS"
    denied = sum(1 for a in actions if a["status"] == "denied")
    if denied >= 3 or "policy" in e or "not approved" in e or "refus" in e:
        return "POLICY"
    return "OTHER"


def build_policy(spec: TaskSpec, sandbox: Path) -> Policy:
    cfg: dict = {"ui_allowed_windows": list(spec.windows), "ui_allowed_hosts": list(spec.hosts),
                 "restrict_input_to_approved": bool(spec.windows or spec.hosts), "cloud_safe": True,
                 "allowed_dirs": [str(sandbox)], "approved_apps": dict(spec.approved_apps), "max_type_len": 300}
    cfg.update(spec.policy)
    return Policy.from_dict(cfg)


def _verdict(status: str, checks: list[CheckResult]) -> str:
    state = [c for c in checks if c.hard and c.name != COMPLETED_LABEL]
    bad = [c for c in state if c.passed is False]
    unknown = [c for c in state if c.passed is None]
    if status == "completed":
        if bad:
            return FALSE_SUCCESS                   # Pilot said done, the real state disagrees: the failure that matters
        return INCONCLUSIVE if unknown else PASS
    if not bad and state and not unknown:
        return PARTIAL                             # the state is right but Pilot did not claim it (under-claim)
    return FAIL


async def run_spec(spec: TaskSpec, backend, model, *, config: Config | None = None, workdir: Path | None = None,
                   hard_timeout_pad: float = 30.0, on_event: Callable[[str], None] | None = None) -> dict:
    """Run one spec. `backend` is the device (a real WindowsBackend, or a simulated one in tests)."""
    log = on_event or (lambda s: None)
    own_dir = workdir is None
    tmp = tempfile.TemporaryDirectory(prefix="tp_eval_") if own_dir else None
    sandbox = Path(tmp.name if tmp else workdir)                 # type: ignore[arg-type]
    sandbox.mkdir(parents=True, exist_ok=True)
    try:
        goal = spec.goal.replace("{sandbox}", str(sandbox))
        policy = build_policy(spec, sandbox)
        ex = Executor(backend, policy)
        hub, store = DeviceHub(), Store()
        store.enroll_device("eval")
        hub.register("eval", Loopback(ex))
        cfg = config or Config(settle_delay_s=0.8, ready_timeout_s=15, action_timeout_s=30, confirm_timeout_s=60,
                               screenshot_mode="auto")
        model = TimedModel(model)
        orch = Orchestrator(store, hub, model, policy, cfg)
        if spec.setup:
            r = spec.setup(backend)
            if asyncio.iscoroutine(r):
                await r

        approvals: list[dict] = []
        tid = orch.submit("eval", goal, max_actions=spec.max_actions, timeout_s=spec.timeout_s)

        async def decide():
            while True:
                await asyncio.sleep(0.1)
                p = orch.pending_approval(tid)
                if p:
                    ok = bool(spec.approve and spec.approve(p))   # nothing is approved unless the spec says so
                    approvals.append({"tool": p.get("tool"), "granted": ok,
                                      "why": str(p.get("reason") or p.get("question") or "")[:120]})
                    orch.approve(tid, ok)
                    await asyncio.sleep(0.3)
        d = asyncio.create_task(decide())
        t0 = time.time()
        try:
            task = await asyncio.wait_for(orch.wait(tid), spec.timeout_s + hard_timeout_pad)
        except asyncio.TimeoutError:
            await orch.cancel(tid)
            task = store.get_task(tid) or {"status": "failed", "error": "timed out", "result": ""}
            task["error"] = task.get("error") or "timed out (harness)"
        finally:
            d.cancel()
        dur = time.time() - t0

        actions = store.list_actions(tid)
        ctx = EvalContext(spec=spec, task=task, actions=actions, transitions=store.transitions(tid),
                          status=task.get("status", ""), result=task.get("result") or "", error=task.get("error") or "",
                          duration_s=dur, model_calls=model.calls, backend=backend,
                          policy=policy, store=store, sandbox=sandbox, goal=goal)
        checks: list[CheckResult] = []
        for v in spec.verifiers:
            try:
                r = v(ctx)
                checks.append(await r if asyncio.iscoroutine(r) else r)
            except Exception as e:                                 # never let a grader crash the run
                checks.append(CheckResult(getattr(v, "label", "verifier"), None, f"verifier crashed: {e}"[:200]))
        verdict = _verdict(ctx.status, checks)
        if spec.teardown:
            try:
                r = spec.teardown(backend)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass
        steps = [a for a in actions if a["tool"] not in ("done", "invalid")]
        return {
            "id": spec.id, "category": spec.category, "goal": goal, "verdict": verdict, "status": ctx.status,
            "failure": "" if verdict == PASS else classify_failure(ctx.status, ctx.error, actions),
            "duration_s": round(dur, 1), "model_seconds": round(model.seconds, 1), "steps": len(steps), "model_calls": ctx.model_calls,
            "refused": sum(1 for a in actions if a["status"] == "denied"),
            "failed_steps": sum(1 for a in actions if a["status"] == "failed"),
            "result": ctx.result[:1500], "error": ctx.error[:300], "approvals": approvals,
            "checks": [c.as_dict() for c in checks],
            "trace": [{"seq": a["seq"], "tool": a["tool"], "status": a["status"],
                       "what": _what(a), "note": str(a.get("error") or a.get("verification") or a.get("decision") or "")[:140]}
                      for a in actions],
        }
    finally:
        if tmp:
            tmp.cleanup()


_KEYS = ("name", "id", "window", "url", "query", "key", "role", "title", "path", "command")


def _what(a: dict) -> str:
    p = a.get("params") or {}
    args = p.get("args", p) if isinstance(p, dict) else {}
    if not isinstance(args, dict):
        return str(args)[:100]
    return " ".join(f"{k}={str(v)[:40]!r}" for k, v in args.items() if k in _KEYS)[:100]
