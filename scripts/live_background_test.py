"""Live check, on THIS Windows computer, that Deskhand can work while you use the computer.

    python scripts/live_background_test.py [--skip-browser] [--skip-calc]

It does real things on your screen for about a minute (opens Calculator behind your window, opens a browser window of its own) and
checks, with Windows' own API, that the window you had in front never changed. Everything it opens is closed again.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taskpilot.agent.backend import WindowsBackend            # noqa: E402
from taskpilot.agent.executor import Executor                  # noqa: E402
from taskpilot.policy import Policy                            # noqa: E402
from taskpilot.protocol import ActionRequest, AppToolCall, LaunchApplication, Scroll   # noqa: E402

u = ctypes.windll.user32
OUT = Path(os.environ.get("DESKHAND_TEST_OUT", str(ROOT / "evals_out")))
OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool, str]] = []


def title_of(hwnd: int) -> str:
    n = u.GetWindowTextLengthW(hwnd)
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


_n = [0]


def req(action):
    _n[0] += 1
    return ActionRequest(action_id=f"live{_n[0]}", task_id="live", action=action)


async def calc_test(b: WindowsBackend, ex: Executor) -> None:
    print("\n--- Option 3: a desktop app driven while another window stays in front ---")
    if any("calculator" in w["title"].lower() for w in b.list_windows()):
        print("SKIP  a Calculator is already open (not touching yours)")
        return
    front = u.GetForegroundWindow()
    print(f"in front now: {title_of(front)!r}")
    ex.policy.approved_apps["calc"] = r"C:\Windows\System32\calc.exe"
    ex.policy.ui_allowed_windows.append("Calculator")
    b.user_idle_s = lambda: 5.0                                   # pretend the owner used the computer 5 s ago: busy enough to hide the app
    ex.polite.idle_source = b.user_idle_s
    r = await ex.handle(req(LaunchApplication(app_id="calc")))
    check("Calculator launched", r.status == "ok", r.error or "")
    shown = False
    for _ in range(40):
        await asyncio.sleep(0.25)
        shown = any("calculator" in w["title"].lower() for w in b.list_windows())
        if shown:
            break
    await asyncio.sleep(2.0)
    check("Calculator window exists", shown)
    check("the window you had in front is STILL in front after launching", u.GetForegroundWindow() == front,
          f"now in front: {title_of(u.GetForegroundWindow())!r}")

    async def press(name):
        return await ex.handle(req(AppToolCall(name="ui.click", args={"window": "Calculator", "name": name, "role": "Button"})))

    ok = True
    for name in ("Seven", "Plus", "Five", "Equals"):
        r = await press(name)
        ok &= r.status == "ok"
        if r.status != "ok":
            print("   ", name, r.error)
    check("pressed 7 + 5 = by control name", ok)
    check("...and the window in front never changed while doing it", u.GetForegroundWindow() == front,
          f"now in front: {title_of(u.GetForegroundWindow())!r}")
    r = await ex.handle(req(AppToolCall(name="ui.read", args={"window": "Calculator", "id": "CalculatorResults"})))
    text = str((r.data or {}).get("text", ""))
    check("Calculator shows 12", "12" in text, text)
    try:
        shot = b.screenshot_window_titled("Calculator", 1000)
        import base64
        path = OUT / "live_calculator_behind.png"
        path.write_bytes(base64.b64decode(shot.image_b64))
        check("captured Calculator while it was behind other windows", shot.width > 100 and "alculator" in shot.active_window.title,
              f"{shot.width}x{shot.height}, window described as {shot.active_window.title!r}")
        print("      picture saved:", path)
    except Exception as e:                                             # noqa: BLE001
        check("captured Calculator while it was behind other windows", False, f"{type(e).__name__}: {e}")
    check("...and still nothing moved to the front", u.GetForegroundWindow() == front)
    ex.end_task("live")
    subprocess.run(["taskkill", "/F", "/IM", "CalculatorApp.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "calc.exe"], capture_output=True)


async def browser_test(b: WindowsBackend, ex: Executor, ext: bool = False) -> None:
    print("\n--- Option 1: web tasks in a browser window of its own " + ("(through the extension, in YOUR Chrome) ---" if ext else "(debug browser) ---"))
    from taskpilot.agent import cdp
    front = u.GetForegroundWindow()
    print(f"in front now: {title_of(front)!r}")
    ex.policy.ui_allowed_hosts = ["youtube.com"]
    r = await ex.handle(req(AppToolCall(name="web.open", args={"url": "https://www.youtube.com/@SSundeePlus/videos"})))
    check("web.open worked", r.status == "ok", (r.error or "") + str((r.data or {}).get("address", "")))
    await asyncio.sleep(1.0)
    check("the window you had in front is STILL in front after the browser opened", u.GetForegroundWindow() == front,
          f"now in front: {title_of(u.GetForegroundWindow())!r}")
    r = await ex.handle(req(AppToolCall(name="web.links", args={"min_chars": 20, "max_links": 3})))
    check("web.links read the page", r.status == "ok" and "watch?v=" in str((r.data or {}).get("tree", "")))
    r = await ex.handle(req(AppToolCall(name="web.search", args={"query": "NetworkChuck paperclip"})))
    await asyncio.sleep(1.0)
    check("...searching did not take the front either", u.GetForegroundWindow() == front)
    r = await ex.handle(req(AppToolCall(name="web.url", args={})))
    check("the page is on a search, in the agent's window", "results" in str((r.data or {}).get("address", "")),
          str((r.data or {}).get("address", "")))
    # the agent's window is a window of its own, and the owner's tabs were never used
    if ext:
        tabs = cdp._ext().request({"cmd": "tabs"})
        mine = next((t for t in tabs if t["id"] == cdp._OWNED.get("tab")), None)
        others = [t for t in tabs if t["id"] != cdp._OWNED.get("tab")]
        check("the agent works in its own tab of your Chrome", mine is not None, str(mine and mine["url"])[:70])
        check("...in a window of its own, apart from your other tabs",
              mine is not None and all(t["windowId"] != mine["windowId"] for t in others), f"{len(others)} of your other tabs untouched")
    else:
        targets = [t for t in cdp._get("/json/list") if t.get("type") == "page"]
        check("the agent works in its own tab", cdp._DBG.get("target") in {t["id"] for t in targets})
    # look at it without bringing it forward
    try:
        title = (await ex.handle(req(AppToolCall(name="web.url", args={})))).data.get("title") or "YouTube"
        shot = b.screenshot_window_titled(title, 1000)
        import base64
        path = OUT / "live_agent_browser_behind.png"
        path.write_bytes(base64.b64decode(shot.image_b64))
        check("captured the agent's browser window while it was behind", shot.width > 300, f"{shot.width}x{shot.height}")
        print("      picture saved:", path)
    except Exception as e:                                             # noqa: BLE001
        check("captured the agent's browser window while it was behind", False, f"{type(e).__name__}: {e}")
    check("...and still nothing moved to the front", u.GetForegroundWindow() == front)


async def idle_test(b: WindowsBackend) -> None:
    print("\n--- Option 2: knowing when you are using the computer ---")
    idle = b.user_idle_s()
    check("Windows reports how long ago you last used the mouse or keyboard", idle is not None and idle >= 0, f"{idle}")
    ctx = b.capture_user_context()
    check("it can note what you have in front and where your mouse is", bool(ctx and ctx.get("hwnd")), str(ctx))


async def polite_test(b: WindowsBackend, ex: Executor) -> None:
    print("\n--- Option 2: really waiting while someone uses the mouse (invisible 0-pixel mouse moves stand in for you) ---")
    import threading
    stop = threading.Event()

    def fidget():                                                     # input Windows counts as a person, with no visible effect
        while not stop.is_set():
            u.mouse_event(0x0001, 0, 0, 0, 0)                          # MOUSEEVENTF_MOVE by (0, 0)
            time.sleep(0.15)
    ex.polite.idle_source = b.user_idle_s
    ex.polite.idle_s, ex.polite.max_wait_s, ex.policy.polite_idle_s = 1.5, 20.0, 1.5
    ex.polite._own_at = None
    t = threading.Thread(target=fidget, daemon=True)
    t.start()
    threading.Timer(2.5, stop.set).start()                            # "you" stop after 2.5 s
    time.sleep(0.4)
    idle_while_busy = b.user_idle_s()
    t0 = time.monotonic()
    r = await ex.handle(req(Scroll(x=10, y=10, clicks=0)))             # a step that would take over the mouse
    waited = time.monotonic() - t0
    check("while you were busy it noticed (idle reading was small)", idle_while_busy is not None and idle_while_busy < 1.0, f"{idle_while_busy}")
    check("it waited for you to stop, then carried on (about 2.5 s + 1.5 s)", 3.2 <= waited <= 6.5 and r.status == "ok", f"waited {waited:.1f}s, status {r.status} {r.error or ''}")
    stop.set()
    # a person who never stops: it gives up with a plain message instead of grabbing the mouse
    orig = ex.polite.wait_until_idle
    def traced(task_id=""):
        t0 = time.monotonic()
        ok = orig(task_id)
        print(f"      [trace] wait_until_idle -> {ok} after {time.monotonic() - t0:.2f}s; raw idle now {b.user_idle_s():.2f}s", flush=True)
        return ok
    ex.polite.wait_until_idle = traced
    time.sleep(0.8)                                                   # steps are seconds apart in real use: let Deskhand's own last input age
    ex.polite.max_wait_s = 1.5
    stop2 = threading.Event()
    def fidget2():
        while not stop2.is_set():
            u.mouse_event(0x0001, 0, 0, 0, 0)
            time.sleep(0.15)
    threading.Thread(target=fidget2, daemon=True).start()
    time.sleep(0.3)
    r = await ex.handle(req(Scroll(x=10, y=10, clicks=0)))
    stop2.set()
    check("a person who never pauses: it does not take over, and says why", r.status == "error" and "using the computer" in (r.error or ""),
          f"status={r.status} error={r.error!r} idle={b.user_idle_s()} polite_idle_s={ex.polite.idle_s} max_wait={ex.polite.max_wait_s} own_at_set={ex.polite._own_at is not None}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-browser", action="store_true")
    ap.add_argument("--skip-calc", action="store_true")
    ap.add_argument("--skip-polite", action="store_true")
    ap.add_argument("--ext", action="store_true", help="test through the Deskhand extension in your own Chrome (waits for you to press Connect)")
    a = ap.parse_args()
    b = WindowsBackend()
    policy = Policy.from_dict({"page_wait_s": 0, "polite": True, "polite_idle_s": 2.5, "polite_max_wait_s": 10,
                               "cloud_safe": False, "ui_allowed_windows": ["Calculator"]})
    ex = Executor(b, policy)
    if a.ext:
        from taskpilot.agent.extbridge import get_bridge
        br = get_bridge()
        print("Waiting up to 5 minutes for the extension: in Chrome open chrome://extensions, press the reload arrow on Deskhand,")
        print("click the Deskhand icon, press 'Connect to Deskhand', and click Yes in the window that appears.", flush=True)
        if not br.wait_connected(300):
            check("the extension connected", False, "it never connected")
        else:
            check("the extension connected", True)
            await browser_test(b, ex, ext=True)
        bad = [r for r in results if not r[1]]
        print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
        return 1 if bad else 0
    await idle_test(b)
    if not a.skip_polite:
        await polite_test(b, ex)
    if not a.skip_calc:
        await calc_test(b, ex)
    if not a.skip_browser:
        await browser_test(b, ex)
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
