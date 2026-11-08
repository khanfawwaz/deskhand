"""Run real-world browser tasks through Pilot (real model, real tools, THIS desktop) in PRIVACY MODE.

    python scripts/live_tasks.py youtube
    python scripts/live_tasks.py news
    python scripts/live_tasks.py chatgpt

Privacy mode (Policy.cloud_safe): Pilot opens pages in a PRIVATE browser window (no logged-in account data),
only the approved foreground window is ever screenshotted (cropped; any other window is refused), and window
titles of other apps are never listed. Each task has its own tiny window allowlist, so nothing else is
automated. Approvals are declined unless explicitly allowed for the task (ChatGPT: the Send click).

Env: NVIDIA_API_KEY. TP_LIVE_SHOT=<png path> saves a crop of the foreground window at the end, only if it is
an approved window.
"""
import asyncio
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from taskpilot.agent.backend import WindowsBackend  # noqa: E402
from taskpilot.agent.executor import Executor  # noqa: E402
from taskpilot.devices import DeviceConnection, DeviceHub  # noqa: E402
from taskpilot.hosted import OpenAICompatRuntime  # noqa: E402
from taskpilot.orchestrator import Config, Orchestrator  # noqa: E402
from taskpilot.policy import Policy  # noqa: E402
from taskpilot.store import Store  # noqa: E402

MODEL = os.environ.get("TP_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")

TASKS = {
    "youtube": dict(goal="Open youtube and play the latest video from battle bots",
                    windows=["YouTube", "BattleBots"], approve_send=False, keep_open_on_success=True),
    "news": dict(goal="Research on what is the new AI news today",
                 windows=["Google News"], approve_send=False),
    "chatgpt": dict(goal="Open chatgpt and ask what is the best cheapest 25 kg Servo motor i can find",
                    windows=["ChatGPT"], hosts=["chatgpt.com"], approve_send=True),
    "claude": dict(goal="Open claude and ask what is the best cheapest 25 kg Servo motor i can find",
                   windows=["Claude"], hosts=["claude.ai"], approve_send=True),
}


class Loopback(DeviceConnection):
    def __init__(self, ex): self.ex = ex
    async def execute(self, req, timeout): return await self.ex.handle(req)
    async def query(self, action_id, timeout=5.0): return self.ex.lookup(action_id)
    async def cancel(self, task_id): self.ex.cancel_task(task_id)
    async def estop(self): self.ex.estop()


def foreground_crop(path: str) -> None:
    """Save ONLY the foreground window (never the rest of the desktop)."""
    import mss
    from PIL import Image
    u = ctypes.windll.user32
    r = wintypes.RECT()
    u.GetWindowRect(u.GetForegroundWindow(), ctypes.byref(r))
    box = {"left": max(r.left, 0), "top": max(r.top, 0), "width": r.right - r.left, "height": r.bottom - r.top}
    with mss.MSS() as s:
        raw = s.grab(box)
        Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX").save(path)


def chrome_hwnds(backend) -> set[int]:
    return {w[0] for w in backend._windows() if w[2].lower() in ("chrome.exe", "msedge.exe")}


def close_new_private_windows(backend, before: set[int]) -> int:
    """Close windows this run opened, but ONLY ones proven private (profile button 'Incognito (N)')."""
    from taskpilot.agent import uia
    closed = 0
    with uia._Com():
        auto = uia._auto()
        for hwnd in chrome_hwnds(backend) - before:
            try:
                els = uia._walk(auto.ControlFromHandle(hwnd), True, limit=1500)
            except Exception:
                continue
            if any(e["role"] == "Button" and e["name"].lower().startswith(("incognito", "inprivate")) for e in els):
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)         # WM_CLOSE
                closed += 1
                time.sleep(0.25)
    return closed


def is_send(p: dict) -> bool:
    args = (p.get("params") or {}).get("args") or {}
    return p.get("tool") == "ui.click" and "send" in str(args.get("name", "")).lower()


async def main() -> int:
    name = next((a for a in sys.argv[1:] if not a.startswith("--")), "")
    if name not in TASKS:
        print("tasks:", ", ".join(TASKS))
        return 2
    spec = TASKS[name]
    if not os.environ.get("NVIDIA_API_KEY"):
        print("ABORT: NVIDIA_API_KEY is not set")
        return 2

    policy = Policy.from_dict({"ui_allowed_windows": spec["windows"], "ui_allowed_hosts": spec.get("hosts", []),
                               "restrict_input_to_approved": True,
                               "cloud_safe": True, "max_type_len": 300})
    backend = WindowsBackend()
    ex = Executor(backend, policy)
    hub, store = DeviceHub(), Store()
    store.enroll_device("local")
    hub.register("local", Loopback(ex))
    model = OpenAICompatRuntime(MODEL, "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                                allow_images=True, timeout=90)
    orch = Orchestrator(store, hub, model, policy, Config(
        settle_delay_s=0.8, ready_timeout_s=15, action_timeout_s=30, confirm_timeout_s=60, screenshot_mode="auto"))

    print(f"task: {name}\ngoal: {spec['goal']}\nallowed windows: {spec['windows']} hosts: {spec.get('hosts', [])} | privacy mode on\n")
    before = chrome_hwnds(backend)
    tid = orch.submit("local", spec["goal"], max_actions=34, timeout_s=420)
    seen: list[tuple[dict, bool]] = []

    async def decide():
        while True:
            await asyncio.sleep(0.2)
            if (p := orch.pending_approval(tid)):
                ok = bool(spec["approve_send"]) and is_send(p)      # nothing else is ever approved
                seen.append((p, ok))
                orch.approve(tid, ok)
                await asyncio.sleep(0.5)
    d = asyncio.create_task(decide())
    t0 = time.time()
    try:
        t = await asyncio.wait_for(orch.wait(tid), 450)
    finally:
        d.cancel()

    print(f"=== {name}: status={t['status']} in {time.time()-t0:.0f}s")
    for a in store.list_actions(tid):
        extra = a["error"] or a["verification"] or a["decision"] or ""
        p = a["params"] or {}
        target = p.get("args", p)
        what = " ".join(f"{k}={str(v)[:38]!r}" for k, v in target.items()
                        if k in ("name", "id", "window", "url", "query", "text", "key", "role", "title"))
        print(f"  #{a['seq']:<2} {a['tool']:<18} {a['status']:<9} {what[:66]:<66} {str(extra)[:100]}")
    for p, ok in seen:
        print(f"  approval {'GRANTED' if ok else 'DECLINED'}: {p.get('tool')} - {p.get('reason') or p.get('question')}")
    print("\nRESULT:\n" + (t.get("result") or t.get("error") or "")[:2000])
    w = backend.active_window()
    allowed = bool(w) and policy.ui_window_allowed(w.title, w.process).allowed
    print("\nforeground window:", (w.title if allowed else "(not an approved window - hidden)"))
    if os.environ.get("TP_LIVE_SHOT") and allowed:
        foreground_crop(os.environ["TP_LIVE_SHOT"])
        print("window crop saved")
    opened = len(chrome_hwnds(backend) - before)
    if spec.get("keep_open_on_success") and t["status"] == "completed":
        print(f"browser windows opened by this run: {opened} (left open: that is the result)")
    else:
        print(f"browser windows opened by this run: {opened} -> closed {close_new_private_windows(backend, before)} private ones")
    return 0 if t["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
