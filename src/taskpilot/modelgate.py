"""One shared door to the model API.

Without it, every task talks to the model on its own: ten people starting tasks at once means ten simultaneous calls, the
provider answers "429 too many requests", and ten tasks fail together. The gate puts every call in ONE line:

  * at most `max_concurrent` calls are in flight at once, the rest wait their turn (first come, first served);
  * one workspace can have at most `per_workspace` calls in flight, so one busy person cannot starve the others;
  * an optional requests-per-minute limit spreads calls out;
  * when the provider says "slow down" (429), EVERYONE waits out the cool-down together instead of hammering it;
  * every call is metered (tokens, time, cost) and refused once the daily budget is used up.

It wraps any runtime, so the orchestrator does not know it is there.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict

from .model import ModelContext, ModelRuntime
from .usage import BudgetExceeded, UsageMeter, estimate_tokens, start_collecting, stop_collecting


class ModelGate(ModelRuntime):
    def __init__(self, inner: ModelRuntime, meter: UsageMeter | None = None, *, max_concurrent: int = 6,
                 per_workspace: int = 2, rpm: int = 0, name: str = ""):
        self.inner, self.meter = inner, meter or UsageMeter()
        self.max_concurrent, self.per_workspace, self.rpm = max(1, max_concurrent), max(1, per_workspace), max(0, rpm)
        self.name = name or type(inner).__name__
        self._global = asyncio.Semaphore(self.max_concurrent)
        self._ws: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(self.per_workspace))
        self._stamps: list[float] = []                 # start times of recent calls (for the per-minute limit)
        self._pause_until = 0.0                        # shared cool-down after the provider said "slow down"
        self._penalty = 0.0
        self.in_flight = 0
        self.waiting = 0
        self.served = 0
        self.rate_limited = 0

    @classmethod
    def from_env(cls, inner: ModelRuntime, meter: UsageMeter | None = None) -> "ModelGate":
        e = os.environ.get
        return cls(inner, meter, max_concurrent=int(e("TASKPILOT_MODEL_CONCURRENCY") or 6),
                   per_workspace=int(e("TASKPILOT_MODEL_PER_WORKSPACE") or 2), rpm=int(e("TASKPILOT_MODEL_RPM") or 0))

    # -------------------------------------------------------------- state for the admin page and the app
    def status(self) -> dict:
        return {"in_flight": self.in_flight, "waiting": self.waiting, "max_concurrent": self.max_concurrent,
                "per_workspace": self.per_workspace, "rpm_limit": self.rpm, "served": self.served,
                "rate_limited": self.rate_limited, "cooldown_s": round(max(0.0, self._pause_until - time.monotonic()), 1)}

    # -------------------------------------------------------------- pacing
    async def _pace(self) -> None:
        while True:
            now = time.monotonic()
            wait = self._pause_until - now
            if wait <= 0 and self.rpm:
                self._stamps = [s for s in self._stamps if now - s < 60]
                if len(self._stamps) >= self.rpm:
                    wait = 60 - (now - self._stamps[0]) + 0.05
            if wait <= 0:
                if self.rpm:
                    self._stamps.append(now)
                return
            await asyncio.sleep(min(wait, 5.0))

    def _slow_down(self, retry_after: float = 0.0) -> None:
        self.rate_limited += 1
        self._penalty = min(60.0, max(retry_after, self._penalty * 2 if self._penalty else 4.0))
        self._pause_until = max(self._pause_until, time.monotonic() + self._penalty)

    # -------------------------------------------------------------- the call
    async def propose(self, ctx: ModelContext):
        ws = getattr(ctx, "workspace", "") or ""
        self.meter.check(ws)                                    # refuse early: no waiting in line for a call that will not be allowed
        self.waiting += 1
        entered = False
        try:
            async with self._ws[ws]:
                async with self._global:
                    self.waiting -= 1
                    entered = True
                    self.meter.check(ws)                        # the budget may have run out while we waited
                    await self._pace()
                    self.in_flight += 1
                    sink, tok = start_collecting()
                    t0, status, out = time.monotonic(), "ok", None
                    try:
                        out = await self.inner.propose(ctx)
                        self._penalty = max(0.0, self._penalty / 2 - 0.5)      # healthy again: ease off the penalty
                        return out
                    except asyncio.CancelledError:
                        status = "cancelled"
                        raise
                    except BudgetExceeded:
                        status = "budget"
                        raise
                    except Exception as e:
                        status = "rate_limited" if type(e).__name__ == "RateLimited" else "error"
                        if status == "rate_limited":
                            self._slow_down()
                        raise
                    finally:
                        stop_collecting(tok)
                        self.in_flight -= 1
                        self.served += 1
                        self._record(ws, ctx, sink, (time.monotonic() - t0) * 1000, status, out)
        finally:
            if not entered:
                self.waiting -= 1

    def _record(self, ws: str, ctx: ModelContext, sink: list, ms: float, status: str, out) -> None:
        calls = sink or [{"model": self.name, "prompt": 0, "completion": 0, "kind": "decide"}]
        for i, c in enumerate(calls):
            if not c["prompt"] and not c["completion"] and status == "ok":       # the API did not say: estimate
                c = {**c, "prompt": estimate_tokens(ctx.goal + str(ctx.history[-12:]) + ctx.screen_text),
                     "completion": estimate_tokens(str(out or ""))}
            self.meter.record(workspace=ws, task_id=getattr(ctx, "task_id", ""), model=c["model"], kind=c["kind"],
                              prompt=c["prompt"], completion=c["completion"], latency_ms=ms / len(calls),
                              status=status)
