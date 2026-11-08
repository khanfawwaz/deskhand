"""Independent verifiers: they inspect the real end state and never trust what Pilot or the model said."""
from __future__ import annotations

import asyncio
import inspect
import re
from typing import Awaitable, Callable

from ..agent.gate import window_allowed
from .spec import CheckResult, EvalContext, Verifier


def _named(name: str, hard: bool, fn: Callable[[EvalContext], Awaitable[CheckResult] | CheckResult]) -> Verifier:
    async def run(ctx: EvalContext) -> CheckResult:
        try:
            r = fn(ctx)
            r = await r if inspect.isawaitable(r) else r
        except Exception as e:                      # a broken verifier is inconclusive, never a Pilot failure
            return CheckResult(name, None, f"verifier error: {type(e).__name__}: {str(e)[:160]}", hard)
        r.name, r.hard = name, hard
        return r
    run.label = name                                # type: ignore[attr-defined]
    return run


def _has(text: str, alts: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(a.lower() in t for a in alts)


# ------------------------------------------------------------------ what Pilot reported
def completed() -> Verifier:
    return _named("task reported completed", True, lambda c: CheckResult("", c.status == "completed", f"status={c.status}"))


def result_has(*groups: tuple[str, ...] | str, label: str | None = None) -> Verifier:
    """Every group must have at least one alternative in the final result text."""
    gs = [(g,) if isinstance(g, str) else g for g in groups]

    def fn(c: EvalContext) -> CheckResult:
        missing = [g[0] for g in gs if not _has(c.result, g)]
        return CheckResult("", not missing, "missing: " + ", ".join(missing) if missing else "all present")
    return _named(label or "result mentions " + " + ".join(g[0] for g in gs), True, fn)


def result_min(n: int) -> Verifier:
    return _named(f"result has at least {n} characters", True,
                  lambda c: CheckResult("", len(c.result.strip()) >= n, f"{len(c.result.strip())} chars"))


def result_matches(pattern: str, label: str) -> Verifier:
    rx = re.compile(pattern, re.I)
    return _named(label, True, lambda c: CheckResult("", bool(rx.search(c.result)), f"pattern {pattern!r}"))


# ------------------------------------------------------------------ what Pilot did
def used(*tools: str) -> Verifier:
    return _named("used " + "/".join(tools), True, lambda c: CheckResult(
        "", any(a["tool"] in tools for a in c.executed() if a["status"] == "succeeded"), ""))


def never_used(*tools: str) -> Verifier:
    def fn(c: EvalContext) -> CheckResult:
        bad = [a["tool"] for a in c.executed() if a["tool"] in tools]
        return CheckResult("", not bad, f"used: {sorted(set(bad))}" if bad else "")
    return _named("never used " + "/".join(tools), True, fn)


def max_steps(n: int) -> Verifier:
    return _named(f"at most {n} steps", False, lambda c: CheckResult(
        "", len(c.executed()) <= n, f"{len(c.executed())} steps"))


def max_seconds(s: float) -> Verifier:
    return _named(f"at most {s:.0f}s", False, lambda c: CheckResult("", c.duration_s <= s, f"{c.duration_s:.0f}s"))


def few_refusals(n: int = 2) -> Verifier:
    return _named(f"at most {n} refused/failed steps", False, lambda c: CheckResult(
        "", sum(1 for a in c.actions if a["status"] in ("denied", "failed")) <= n,
        f"{sum(1 for a in c.actions if a['status'] in ('denied', 'failed'))} refused/failed"))


def grounded() -> Verifier:
    """Informational: were Pilot's own 'done' claims supported by what it observed? (rejections are listed)"""
    def fn(c: EvalContext) -> CheckResult:
        rejected = [a for a in c.actions if a["tool"] == "done" and a["status"] == "denied"]
        note = next((t["note"] for t in reversed(c.transitions) if "grounding" in (t.get("note") or "")), "")
        return CheckResult("", not rejected, f"{len(rejected)} unsupported claim(s) rejected; final: {note[-60:]}")
    return _named("claims were supported by observations", False, fn)


# ------------------------------------------------------------------ the real end state (read-only)
async def _approved_windows(c: EvalContext) -> list[dict]:
    wins = await asyncio.to_thread(c.backend.list_windows)
    return [w for w in wins if (await window_allowed(c.policy, c.backend, w["title"], w["process"])).allowed]


def window_title_has(*alts: str, label: str | None = None) -> Verifier:
    """Some APPROVED window's title contains one of the alternatives (other windows are never read)."""
    async def fn(c: EvalContext) -> CheckResult:
        titles = [w["title"] for w in await _approved_windows(c)]
        hit = next((t for t in titles if _has(t, alts)), None)
        return CheckResult("", hit is not None, f"title: {hit!r}" if hit else f"{len(titles)} approved window(s), none match")
    return _named(label or "a window title contains " + " / ".join(alts), True, fn)


def page_has(window: str, *needles: str, label: str | None = None, present: bool = True) -> Verifier:
    async def fn(c: EvalContext) -> CheckResult:
        win = await asyncio.to_thread(c.backend.ui_window, window)
        if win is None:
            return CheckResult("", False if present else True, f"no window matching {window!r}")
        if not (await window_allowed(c.policy, c.backend, win["title"], win["process"])).allowed:
            return CheckResult("", None, "window is not approved: not read")
        found, missing = [], []
        for n in needles:
            snap = await asyncio.to_thread(c.backend.ui_snapshot, win, 3, n, 200)
            (found if snap["count"] > 0 else missing).append(n)
        ok = (not missing) if present else (not found)
        return CheckResult("", ok, f"found {found}" + (f", missing {missing}" if missing else ""))
    return _named(label or (("page shows " if present else "page does not show ") + " + ".join(needles)), True, fn)


def control_value(window: str, selector: dict, *, equals: str | None = None, contains: str | None = None,
                  label: str | None = None) -> Verifier:
    async def fn(c: EvalContext) -> CheckResult:
        win = await asyncio.to_thread(c.backend.ui_window, window)
        if win is None:
            return CheckResult("", False, f"no window matching {window!r}")
        r = await asyncio.to_thread(c.backend.ui_act, win, {"role": "", "nth": None, **selector}, "read", None)
        if "error" in r:
            return CheckResult("", False, r["error"][:150])
        text = str(r.get("text", ""))
        ok = (text.strip() == equals) if equals is not None else (contains.lower() in text.lower())
        return CheckResult("", ok, f"control reads {text[:80]!r}")
    return _named(label or f"control {selector} reads {equals or contains!r}", True, fn)


def file_text(relpath: str, *, equals: str | None = None, contains: str | None = None, label: str | None = None) -> Verifier:
    def fn(c: EvalContext) -> CheckResult:
        f = c.sandbox / relpath
        if not f.exists():
            return CheckResult("", False, f"{relpath} does not exist")
        text = f.read_text(encoding="utf-8", errors="replace")
        ok = text.strip() == equals if equals is not None else (contains or "") in text
        return CheckResult("", ok, f"{relpath}: {text[:60]!r}")
    return _named(label or f"file {relpath} has the right content", True, fn)


def external(label: str, fn: Callable[[EvalContext], Awaitable[tuple[bool | None, str]] | tuple[bool | None, str]]) -> Verifier:
    """Compare against TRUTH from outside Pilot (a public page, a computed value). (None, why) = inconclusive."""
    async def run(c: EvalContext) -> CheckResult:
        r = fn(c)
        ok, why = await r if inspect.isawaitable(r) else r
        return CheckResult("", ok, why)
    return _named(label, True, run)
