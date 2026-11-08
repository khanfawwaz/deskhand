"""Live UI Automation + recipe test on THIS Windows desktop, using the real Calculator.

Part A (no model, no API key): scripted run through the real orchestrator/agent -> draft recipe -> approve ->
         replay with the model never consulted -> permission gate check -> zoom crop geometry.
Part B (needs NVIDIA_API_KEY; skipped without it): the real model learns Calculator by itself.

UI Automation invokes controls directly: the mouse and keyboard should not move. The script asserts that
no click fell back to the physical mouse. Closes the Calculator it opened. Aborts if Calculator is already open.

    python scripts/live_ui_test.py [--no-model] [--fresh] [model-id]
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psutil  # noqa: E402

from taskpilot.agent.backend import WindowsBackend  # noqa: E402
from taskpilot.agent.executor import Executor  # noqa: E402
from taskpilot.devices import DeviceConnection, DeviceHub  # noqa: E402
from taskpilot.hosted import OpenAICompatRuntime  # noqa: E402
from taskpilot.model import ModelRuntime, ScriptedRuntime  # noqa: E402
from taskpilot.orchestrator import Config, Orchestrator  # noqa: E402
from taskpilot.policy import Policy  # noqa: E402
from taskpilot.protocol import ActionAdapter, ActionRequest  # noqa: E402
from taskpilot.recipes import parametrize, sync_recipe_tools, validate_recipe  # noqa: E402
from taskpilot.store import Store  # noqa: E402
from taskpilot.tools import get_registry  # noqa: E402

NO_MODEL = "--no-model" in sys.argv
FRESH = "--fresh" in sys.argv          # hide the saved recipe from the model: Part B then LEARNS the app
_pos = [a for a in sys.argv[1:] if not a.startswith("--")]
MODEL = _pos[0] if _pos else "nvidia/nemotron-3.5-super-vl-120b-a12b"
POLICY = Policy.from_dict({"approved_apps": {"calc": r"C:\Windows\System32\calc.exe"},
                           "ui_allowed_windows": ["Calculator"],
                           "restrict_input_to_approved": True})     # raw clicks/keys only reach approved apps


class Loopback(DeviceConnection):
    def __init__(self, ex): self.ex = ex
    async def execute(self, req, timeout): return await self.ex.handle(req)
    async def query(self, action_id, timeout=5.0): return self.ex.lookup(action_id)
    async def cancel(self, task_id): self.ex.cancel_task(task_id)
    async def estop(self): self.ex.estop()


def act(tool, **p):
    return {"reasoning": "scripted", "action": {"tool": tool, **p}}


def kill_calculator(backend) -> None:
    for p in psutil.process_iter():
        if p.name().lower() in ("calculatorapp.exe", "calc.exe", "calculator.exe"):
            try:
                p.kill()
            except Exception:
                pass
    for _ in range(40):
        time.sleep(0.25)
        if backend.ui_window("Calculator") is None:
            return


def display(backend) -> str:
    w = backend.ui_window("Calculator")
    return backend.ui_act(w, {"id": "CalculatorResults"}, "read", None).get("text", "?") if w else "(no window)"


def steps(store, tid) -> None:
    for a in store.list_actions(tid):
        print(f"    #{a['seq']:<2} {a['tool']:<44} {a['status']:<10} {str(a['verification'] or a['error'] or '')[:70]}")


def no_mouse_fallback(store, tids) -> bool:
    methods = [(a["result"] or {}).get("method") for t in tids for a in store.list_actions(t) if "ui.click" in a["tool"]]
    print(f"    ui.click methods used: {sorted(set(map(str, methods)))}")
    return bool(methods) and all(m == "invoke" for m in methods)


async def main() -> int:
    backend = WindowsBackend()
    if backend.ui_window("Calculator") is not None:
        print("ABORT: Calculator is already open - close it first so we do not touch your session.")
        return 2
    ex = Executor(backend, POLICY)
    hub, store = DeviceHub(), Store()
    store.enroll_device("local")
    hub.register("local", Loopback(ex))
    results: list[tuple[str, bool, str]] = []
    cfg = dict(settle_delay_s=0.5, ready_timeout_s=15, action_timeout_s=30, confirm_timeout_s=60, screenshot_mode="auto")

    try:
        # ------------------------------------------------------------------ A1: scripted run, records a draft
        script = [act("launch_application", app_id="calc"), act("ui.snapshot", window="Calculator"),
                  act("ui.click", window="Calculator", name="Seven", role="Button"),
                  act("ui.click", window="Calculator", name="Plus"), act("ui.click", window="Calculator", name="Five"),
                  act("ui.click", window="Calculator", name="Equals"),
                  act("ui.read", window="Calculator", id="CalculatorResults"),
                  {"outcome": {"outcome": "done", "evidence": "ui.read returned the display text", "message": "7+5 done"}}]
        orch = Orchestrator(store, hub, ScriptedRuntime(script), POLICY, Config(record_recipes=True, **cfg))
        t0 = time.time()
        tid = orch.submit("local", "add 7 and 5 in Calculator", max_actions=12, timeout_s=90)
        t = await orch.wait(tid)
        shown = display(backend)
        print(f"\n--- A1 scripted run ({time.time()-t0:.1f}s): status={t['status']} display={shown!r}")
        steps(store, tid)
        ok = t["status"] == "completed" and shown == "Display is 12"
        results.append(("A1 drive real Calculator by control name", ok, f"status={t['status']} display={shown!r}"))
        results.append(("A1 no click fell back to the physical mouse", no_mouse_fallback(store, [tid]), "invoke only"))

        # ------------------------------------------------------------------ A2: draft -> approve -> replay without the model
        draft = store.get_recipe(f"draft_{tid}")
        print(f"\n--- A2 draft recorded: {[s['tool'] + ':' + str(s.get('name') or s.get('app_id') or '') for s in (draft or {}).get('steps', [])]}")
        results.append(("A2 draft recipe saved from the run", bool(draft) and draft["status"] == "draft", f"draft={bool(draft)}"))
        if draft:
            rec = parametrize(draft, {"a": "Seven", "b": "Five"})
            rec.update(name="calc_add", status="approved", description="Add two numbers in Calculator",
                       verify=[{"step": {"tool": "ui.read", "window": "Calculator", "id": "CalculatorResults"},
                                "contains": "Display is"}])
            problems = validate_recipe(rec)
            print(f"    approval problems: {problems or 'none'}")
            store.save_recipe(rec)
            sync_recipe_tools(get_registry(), store)
            kill_calculator(backend)                                  # replay from a cold start (includes the launch)
            t0 = time.time()
            tid2 = orch.submit_recipe("local", "calc_add", {"a": "Nine", "b": "Five"}, timeout_s=90)
            t2 = await orch.wait(tid2)
            shown2 = display(backend)
            print(f"\n--- A2 replay, model never asked ({time.time()-t0:.1f}s): status={t2['status']} display={shown2!r}")
            steps(store, tid2)
            results.append(("A2 replay recipe 9+5 with no model", t2["status"] == "completed" and shown2 == "Display is 14",
                            f"status={t2['status']} display={shown2!r} err={t2.get('error')}"))
            results.append(("A2 replay used no mouse", no_mouse_fallback(store, [tid2]), "invoke only"))

        # ------------------------------------------------------------------ A3: permission gate on a real other window
        others = [w for w in backend.list_windows() if "calc" not in w["title"].lower() and w["title"].strip()]
        if others:
            w = others[0]
            r = await ex.handle(ActionRequest(action_id="gate1", task_id="t", action=ActionAdapter.validate_python(
                {"tool": "app_tool", "name": "ui.snapshot", "args": {"window": w["title"]}})))
            print(f"\n--- A3 gate on another real window (process {w['process']}): status={r.status} -> {str(r.error)[:70]!r}")
            results.append(("A3 other windows are refused (title not shown)", r.status == "error" and "approved" in str(r.error).lower() + "protected",
                            f"{w['process']}: {r.status}"))

        # ------------------------------------------------------------------ A4: zoom geometry on the real display
        z = backend.screenshot_region(100, 100, 300, 200, 1000)
        print(f"\n--- A4 zoom crop of display region (100,100,300x200): image {z.width}x{z.height}, scale {z.scale:.3f}, origin ({z.origin_x},{z.origin_y})")
        results.append(("A4 zoom enlarges a region and keeps its origin",
                        z.width > 300 and abs(z.scale * z.width - 300) < 1.5 and (z.origin_x, z.origin_y) == (100, 100),
                        f"{z.width}x{z.height} scale={z.scale:.3f}"))

        # ------------------------------------------------------------------ B: the real model learns Calculator
        if NO_MODEL or not os.environ.get("NVIDIA_API_KEY"):
            print("\n--- B skipped (no key or --no-model)")
        else:
            kill_calculator(backend)
            if FRESH:
                for rname in [n for n in get_registry().names() if n.startswith("recipe.")]:
                    get_registry().unregister(rname)
                print("\n--- B runs WITHOUT the saved recipe: the model must learn Calculator itself")
            model = OpenAICompatRuntime(MODEL, "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                                        allow_images=True, timeout=90)
            orchB = Orchestrator(store, hub, model, POLICY, Config(**cfg))
            t0 = time.time()
            tidB = orchB.submit("local", "Open Calculator, add 8 and 6, and tell me what the display shows",
                                max_actions=22, timeout_s=180)
            tB = await orchB.wait(tidB)
            shownB = display(backend)
            print(f"\n--- B real model ({time.time()-t0:.1f}s): status={tB['status']} display={shownB!r}")
            print(f"    model said: {(tB.get('result') or tB.get('error') or '')[:150]}")
            steps(store, tidB)
            lat = sorted(m["value_ms"] for m in store.metrics("model_latency"))
            results.append(("B real model drives Calculator it has never seen", tB["status"] == "completed" and shownB == "Display is 14",
                            f"status={tB['status']} display={shownB!r}"))
            used = {a["tool"] for a in store.list_actions(tidB)}
            print(f"    tools used: {sorted(used)}   model calls: {len(lat)}")
    finally:
        kill_calculator(backend)
        for rname in [n for n in get_registry().names() if n.startswith("recipe.")]:
            get_registry().unregister(rname)

    print("\n=== SUMMARY ===")
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<52} {detail}")
    print("calculator left open:", backend.ui_window("Calculator") is not None)
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
