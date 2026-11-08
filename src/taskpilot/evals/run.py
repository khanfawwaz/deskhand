"""CLI:  python -m taskpilot.evals.run --suite sim|live [--tasks a,b] [--repeats N] [--out evals_out] [--include-heavy]

Pilot runs every task unaided; verifiers grade the real end state. Heavy tasks (they type into third-party chat sites)
are skipped unless --include-heavy or named explicitly with --tasks.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from ..orchestrator import Config
from . import report
from .runner import run_spec
from .suites import get_suite


def _model(images: bool = True):
    from ..hosted import OpenAICompatRuntime
    name = os.environ.get("TP_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
    return name, OpenAICompatRuntime(name, "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                                     allow_images=images, timeout=90)


def _load_dotenv() -> None:
    """Read KEY=value lines from ./.env (never overriding real environment variables)."""
    f = Path(".env")
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taskpilot.evals.run")
    ap.add_argument("--suite", default="sim", choices=["sim", "live"])
    ap.add_argument("--tasks", default="", help="comma-separated task ids or substrings")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", default="evals_out")
    ap.add_argument("--include-heavy", action="store_true")
    ap.add_argument("--no-learn", action="store_true", help="do not use or store experience memory")
    a = ap.parse_args(argv)
    _load_dotenv()
    if not os.environ.get("NVIDIA_API_KEY"):
        print("ABORT: NVIDIA_API_KEY is not set")
        return 2
    wanted = [t for t in a.tasks.split(",") if t]
    specs = [s for s in get_suite(a.suite)
             if (any(w in s.id for w in wanted) if wanted else (a.include_heavy or "heavy" not in s.tags))]
    if not specs:
        print("no tasks selected")
        return 2
    # the simulated desktop has no real pixels: a fake image is rejected by the vision API, so sim runs are text-only
    model_name, model = _model(images=a.suite == "live")
    backend = None
    if a.suite == "live":
        from ..agent.backend import WindowsBackend
        backend = WindowsBackend()
    results = []
    for rep in range(a.repeats):
        for s in specs:
            print(f"[{rep + 1}/{a.repeats}] {s.id}: {s.goal.replace('{sandbox}', '<tmp>')[:80]}", flush=True)
            be = s.world() if s.world else backend
            cfg = Config(settle_delay_s=0.0 if a.suite == "sim" else 0.8, ready_timeout_s=15, action_timeout_s=30,
                         confirm_timeout_s=60, screenshot_mode="auto", learn=not a.no_learn)
            r = await run_spec(s, be, model, config=cfg)
            results.append(r)
            print(f"    -> {r['verdict']} ({r['status']}, {r['steps']} steps, {r['duration_s']}s, model {r['model_seconds']}s over {r['model_calls']} calls)"
                  + (f" [{r['failure']}]" if r["failure"] else "")
                  + (f" | error: {r['error'][:160]}" if r["error"] else ""), flush=True)
    out = Path(a.out)
    before = report.last_summary(out)
    paths = report.save(results, out, {"suite": a.suite, "model": model_name, "repeats": a.repeats})
    s = report.summarize(results)
    print(f"\n{s['verdicts']}  pass rate {s['pass_rate']:.0%}  false successes {s['false_success_rate']:.0%}")
    if before:
        c = report.compare(before, s)
        print("regressions:", c["regressions"] or "none", "| improvements:", c["improvements"] or "none")
    print("report:", paths["markdown"])
    return 0 if s["verdicts"].get("FALSE_SUCCESS", 0) == 0 and s["pass_rate"] > 0 else 1


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
