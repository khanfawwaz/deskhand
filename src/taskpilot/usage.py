"""Usage tracking and the spend cap.

Every model call is recorded: who (workspace), which task, which model, how many tokens, how long, and what it cost at the
prices you configured. The meter also enforces a daily budget for the WHOLE service (and, optionally, a daily token limit
per workspace), so a public sign-up cannot run up an unlimited bill.

Environment (all optional; 0 or unset means "no limit" / "free"):
  TASKPILOT_PRICE_IN_PER_M       price of 1,000,000 prompt tokens, in dollars
  TASKPILOT_PRICE_OUT_PER_M      price of 1,000,000 completion tokens, in dollars
  TASKPILOT_DAILY_BUDGET_USD     stop all model calls for the day once this much has been spent (needs the two prices)
  TASKPILOT_DAILY_TOKEN_CAP      stop all model calls for the day after this many tokens, whatever they cost
  TASKPILOT_WS_TOKENS_PER_DAY    most tokens one workspace may use per day
"""
from __future__ import annotations

import calendar
import contextvars
import logging
import os
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("taskpilot.usage")

# While a model call is running, runtimes report the token counts they were given here (set by the model gate).
_SINK: contextvars.ContextVar[list | None] = contextvars.ContextVar("usage_sink", default=None)


def start_collecting() -> tuple[list, contextvars.Token]:
    sink: list = []
    return sink, _SINK.set(sink)


def stop_collecting(token: contextvars.Token) -> None:
    _SINK.reset(token)


def report_usage(model: str, prompt_tokens: int, completion_tokens: int, kind: str = "decide") -> None:
    """Called by a runtime after each API response. Does nothing when nobody is collecting."""
    sink = _SINK.get()
    if sink is not None:
        sink.append({"model": model, "prompt": int(prompt_tokens or 0), "completion": int(completion_tokens or 0), "kind": kind})


def estimate_tokens(text: str) -> int:
    """A rough count when the API does not say (about four characters per token)."""
    return max(1, len(text or "") // 4)


class BudgetExceeded(Exception):
    """The service-wide or per-workspace daily limit is used up. The message is safe to show to a user."""


def _day_start(now: float | None = None) -> float:
    t = time.gmtime(now or time.time())
    return float(calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0)))                    # midnight UTC


@dataclass
class Limits:
    price_in: float = 0.0               # dollars per million prompt tokens
    price_out: float = 0.0              # dollars per million completion tokens
    daily_budget_usd: float = 0.0       # 0 = no cap
    daily_token_cap: int = 0            # 0 = no cap
    ws_tokens_per_day: int = 0          # 0 = no cap

    @classmethod
    def from_env(cls) -> "Limits":
        f = lambda k, d=0.0: float(os.environ.get(k) or d)            # noqa: E731
        return cls(price_in=f("TASKPILOT_PRICE_IN_PER_M"), price_out=f("TASKPILOT_PRICE_OUT_PER_M"),
                   daily_budget_usd=f("TASKPILOT_DAILY_BUDGET_USD"), daily_token_cap=int(f("TASKPILOT_DAILY_TOKEN_CAP")),
                   ws_tokens_per_day=int(f("TASKPILOT_WS_TOKENS_PER_DAY")))


@dataclass
class _Day:
    start: float = 0.0
    calls: int = 0
    prompt: int = 0
    completion: int = 0
    cost: float = 0.0
    errors: int = 0
    by_ws: dict = field(default_factory=dict)       # workspace -> {"tokens": n, "calls": n, "cost": x}


class UsageMeter:
    """Counts today's usage in memory (fast) and writes every call to the store (durable)."""

    def __init__(self, store=None, limits: Limits | None = None):
        self.store, self.limits = store, limits or Limits()
        self._lock = threading.Lock()
        self._day = _Day(start=_day_start())
        self._load_today()
        if self.limits.daily_budget_usd and not (self.limits.price_in or self.limits.price_out):
            log.warning("TASKPILOT_DAILY_BUDGET_USD is set but no token prices are: the cap can never trigger. "
                        "Set TASKPILOT_PRICE_IN_PER_M / TASKPILOT_PRICE_OUT_PER_M, or use TASKPILOT_DAILY_TOKEN_CAP.")

    # ------------------------------------------------------------ today's totals
    def _load_today(self) -> None:
        d = _Day(start=_day_start())
        if self.store is not None and hasattr(self.store, "usage_since"):
            try:
                s = self.store.usage_since(d.start)
                d.calls, d.prompt, d.completion, d.cost, d.errors = (s["calls"], s["prompt"], s["completion"], s["cost"], s["errors"])
                d.by_ws = {w["workspace"]: {"tokens": w["prompt"] + w["completion"], "calls": w["calls"], "cost": w["cost"]}
                           for w in s["by_workspace"]}
            except Exception:                                       # a broken meter must never stop the service
                log.exception("could not load today's usage")
        self._day = d

    def _roll(self) -> None:
        if _day_start() > self._day.start:
            self._load_today()

    def cost_of(self, prompt: int, completion: int) -> float:
        return prompt * self.limits.price_in / 1e6 + completion * self.limits.price_out / 1e6

    # ------------------------------------------------------------ limits
    def check(self, workspace: str = "") -> None:
        """Raise BudgetExceeded when a daily limit is used up. Called before every model call."""
        lim = self.limits
        with self._lock:
            self._roll()
            d = self._day
            tokens = d.prompt + d.completion
            if lim.daily_budget_usd and d.cost >= lim.daily_budget_usd:
                raise BudgetExceeded("Deskhand has used up its model budget for today. It resets at midnight UTC: please try again then.")
            if lim.daily_token_cap and tokens >= lim.daily_token_cap:
                raise BudgetExceeded("Deskhand has reached its daily usage limit. It resets at midnight UTC: please try again then.")
            if lim.ws_tokens_per_day and workspace and d.by_ws.get(workspace, {}).get("tokens", 0) >= lim.ws_tokens_per_day:
                raise BudgetExceeded("This workspace has reached its daily usage limit. It resets at midnight UTC.")

    # ------------------------------------------------------------ recording
    def record(self, *, workspace: str, task_id: str, model: str, kind: str, prompt: int, completion: int,
               latency_ms: float, status: str) -> float:
        cost = self.cost_of(prompt, completion)
        with self._lock:
            self._roll()
            d = self._day
            d.calls += 1
            d.prompt += prompt
            d.completion += completion
            d.cost += cost
            if status != "ok":
                d.errors += 1
            w = d.by_ws.setdefault(workspace or "", {"tokens": 0, "calls": 0, "cost": 0.0})
            w["tokens"] += prompt + completion
            w["calls"] += 1
            w["cost"] += cost
        if self.store is not None and hasattr(self.store, "add_usage"):
            try:
                self.store.add_usage({"ts": time.time(), "workspace": workspace or "", "task_id": task_id or "", "model": model,
                                      "kind": kind, "prompt": prompt, "completion": completion, "latency_ms": round(latency_ms, 1),
                                      "status": status, "cost": cost})
            except Exception:
                log.exception("could not store a usage record")
        return cost

    # ------------------------------------------------------------ reporting
    def today(self, workspace: str | None = None) -> dict:
        with self._lock:
            self._roll()
            d = self._day
            if workspace is not None:
                w = d.by_ws.get(workspace, {"tokens": 0, "calls": 0, "cost": 0.0})
                return {"calls": w["calls"], "tokens": w["tokens"], "token_limit": self.limits.ws_tokens_per_day}
            lim = self.limits
            return {"calls": d.calls, "errors": d.errors, "prompt_tokens": d.prompt, "completion_tokens": d.completion,
                    "tokens": d.prompt + d.completion, "cost_usd": round(d.cost, 4),
                    "budget_usd": lim.daily_budget_usd, "token_cap": lim.daily_token_cap,
                    "budget_used": (round(d.cost / lim.daily_budget_usd, 3) if lim.daily_budget_usd else None),
                    "workspaces_today": len(d.by_ws), "resets_at": self._day.start + 86400}
