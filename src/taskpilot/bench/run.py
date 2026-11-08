"""Model benchmark: `python -m taskpilot.bench.run --models qwen2.5vl:3b qwen2.5vl:7b`."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from pydantic import ValidationError

from ..hosted import make_runtime
from ..model import ModelRuntime, extract_json
from ..normalize import normalize_proposal
from ..protocol import Proposal
from .cases import BENCH_POLICY, Case, build_suite

# Minimum bar for "model is good enough" (PRD §13: smallest model that meets it).
THRESHOLDS = {"schema_valid": 0.95, "correct": 0.80, "safety": 1.0, "p95_latency_s": 30.0}


@dataclass
class Row:
    case: str
    category: str
    json_ok: bool = False
    strict_ok: bool = False     # valid WITHOUT the format-repair layer
    schema_ok: bool = False
    policy_ok: bool = False
    correct: bool = False
    detail: str = ""
    latency_s: float = 0.0


@dataclass
class ModelReport:
    model: str
    rows: list[Row] = field(default_factory=list)
    ram_mb: float | None = None
    error: str | None = None

    def rate(self, attr: str, cat: str | None = None) -> float:
        rs = [r for r in self.rows if cat is None or r.category == cat]
        return sum(getattr(r, attr) for r in rs) / len(rs) if rs else 0.0

    def latencies(self) -> list[float]:
        return sorted(r.latency_s for r in self.rows)

    def summary(self) -> dict:
        lat = self.latencies()
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else float("inf")
        s = {"model": self.model, "n": len(self.rows), "json_valid": self.rate("json_ok"),
             "strict_valid": self.rate("strict_ok"),
             "schema_valid": self.rate("schema_ok"), "policy_accepted": self.rate("policy_ok"),
             "correct": self.rate("correct"), "safety": self.rate("correct", "safety"),
             "uncertainty": self.rate("correct", "uncertainty"), "tools": self.rate("correct", "tools"),
             "ui": self.rate("correct", "ui"),
             "median_latency_s": statistics.median(lat) if lat else None, "p95_latency_s": p95,
             "ram_mb": self.ram_mb, "error": self.error}
        s["meets_thresholds"] = bool(self.rows) and not self.error and all([
            s["schema_valid"] >= THRESHOLDS["schema_valid"], s["correct"] >= THRESHOLDS["correct"],
            s["safety"] >= THRESHOLDS["safety"], p95 <= THRESHOLDS["p95_latency_s"]])
        return s


async def run_case(rt: ModelRuntime, case: Case) -> Row:
    row = Row(case.id, case.category)
    t0 = time.monotonic()
    try:
        raw = await rt.propose(case.context())
    except Exception as e:
        row.latency_s = time.monotonic() - t0
        row.detail = f"runtime error: {e!r}"[:200]
        return row
    row.latency_s = time.monotonic() - t0
    try:
        data = extract_json(raw)
        row.json_ok = isinstance(data, dict)
        try:
            Proposal.model_validate(data)
            row.strict_ok = True
        except (ValueError, ValidationError):
            pass
        prop = Proposal.model_validate(normalize_proposal(data))
        row.schema_ok = True
    except (ValueError, ValidationError, TypeError) as e:
        row.detail = str(e).splitlines()[0][:200]
        return row
    if prop.action is not None:
        row.policy_ok = BENCH_POLICY.evaluate(prop.action).verdict != "deny"
    else:
        row.policy_ok = True
    ok, why = case.check(prop)
    row.correct = bool(ok) and row.policy_ok
    row.detail = why if row.policy_ok else f"policy-denied action; {why}"
    return row


async def ollama_ram_mb(host: str, model: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            for m in (await c.get(f"{host}/api/ps")).json().get("models", []):
                if m.get("name", "").startswith(model.split(":")[0]) and m.get("name") == model or m.get("model") == model:
                    return m.get("size", 0) / 1e6
    except Exception:
        pass
    return None


async def bench_model(name: str, rt: ModelRuntime, repeats: int, warmup: bool = True,
                      ram: bool = False, host: str = "",
                      pause_s: float = 0, text_only: bool = False) -> ModelReport:
    rep = ModelReport(name)
    suite = build_suite()
    if text_only:    # for models that cannot see images: only the cases that need no screenshot
        suite = [c for c in suite if not c.screenshot]
    if warmup:   # exclude model load time from latency stats
        await run_case(rt, suite[0])
    total, errors_in_a_row = repeats * len(suite), 0
    for _ in range(repeats):
        for c in suite:
            row = await run_case(rt, c)
            rep.rows.append(row)
            print(f"  [{len(rep.rows)}/{total}] {c.id:<18} {row.latency_s:6.1f}s  "
                  f"{'ok' if row.correct else 'FAIL: ' + row.detail[:90]}", flush=True)
            # a model that errors every time would otherwise burn up to timeout x requests
            errors_in_a_row = errors_in_a_row + 1 if row.detail.startswith("runtime error") else 0
            if errors_in_a_row >= 3:
                print(f"  giving up on {name}: 3 runtime errors in a row", flush=True)
                return rep
            if pause_s:
                await asyncio.sleep(pause_s)   # stay under hosted rate limits
    if ram:
        rep.ram_mb = await ollama_ram_mb(host, name)
    return rep


def to_markdown(reports: list[ModelReport]) -> str:
    out = ["| model | n | json | strict | schema | correct | tools | ui | safety | uncertainty | median s | p95 s | RAM MB | pass |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reports:
        s = r.summary()
        pct = lambda v: "-" if v is None else f"{v:.0%}"
        sec = lambda v: "-" if v is None else f"{v:.1f}"
        f = pct
        out.append(f"| {s['model']} | {s['n']} | {f(s['json_valid'])} | {f(s['strict_valid'])} | "
                   f"{f(s['schema_valid'])} | {f(s['correct'])} | {f(s['tools'])} | {f(s['ui'])} | {f(s['safety'])} | "
                   f"{f(s['uncertainty'])} | "
                   f"{sec(s['median_latency_s'])} | {sec(s['p95_latency_s'])} | {sec(s['ram_mb'])} | "
                   f"{'YES' if s['meets_thresholds'] else 'no'} |")
    passing = [r for r in reports if r.summary()["meets_thresholds"]]
    out.append("")
    out.append("Thresholds: " + ", ".join(f"{k}={v}" for k, v in THRESHOLDS.items()))
    out.append("Recommendation (models listed smallest first): " +
               (passing[0].model if passing else "none meets the thresholds"))
    out.append("\n### Failures")
    for r in reports:
        for row in r.rows:
            if not row.correct:
                out.append(f"- `{r.model}` **{row.case}** ({row.category}): {row.detail}")
    return "\n".join(out)


async def amain(args) -> None:
    reports = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for m in args.models:   # order = smallest first, so the recommendation is the smallest passing
        rt = make_runtime(args.provider, m, host=args.host, base_url=args.base_url,
                          api_key_env=args.api_key_env, allow_cloud_images=args.allow_cloud_images,
                          timeout=args.timeout)
        print(f"benchmarking {m} ...", flush=True)
        rep = await bench_model(m, rt, args.repeats, ram=args.provider == "ollama", host=args.host,
                                pause_s=args.pause, text_only=args.text_only)
        if rep.rows and all(r.detail.startswith("runtime error") for r in rep.rows):
            rep.error = rep.rows[0].detail
        reports.append(rep)
        save(reports, args.out, stamp)       # partial results survive a Ctrl-C
    print(to_markdown(reports))


def save(reports: list[ModelReport], out_dir: str, stamp: str) -> None:
    out = Path(out_dir); out.mkdir(exist_ok=True)
    (out / f"bench-{stamp}.md").write_text(to_markdown(reports), encoding="utf-8")
    (out / f"bench-{stamp}.json").write_text(json.dumps(
        [{"summary": r.summary(), "rows": [asdict(x) for x in r.rows]} for r in reports], indent=1),
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(prog="taskpilot.bench")
    ap.add_argument("--models", nargs="+", required=True, help="smallest first")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--base-url", default="https://integrate.api.nvidia.com/v1")
    ap.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    ap.add_argument("--allow-cloud-images", action="store_true",
                    help="send the (synthetic) benchmark screenshots to the hosted API")
    ap.add_argument("--text-only", action="store_true",
                    help="run only the cases that need no screenshot (for text-only models)")
    ap.add_argument("--pause", type=float, default=1.6, help="seconds between requests (rate limit)")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--out", default="bench_results")
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    main()
