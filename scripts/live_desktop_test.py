"""Live desktop test: real orchestrator + real executor + real WindowsBackend, scripted "model".

Takes over the mouse/keyboard for ~20 s. Move the mouse into a screen corner to abort (pyautogui fail-safe).
Run:  python scripts/live_desktop_test.py
"""
import asyncio
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psutil  # noqa: E402

from taskpilot.agent.backend import WindowsBackend  # noqa: E402
from taskpilot.agent.executor import Executor  # noqa: E402
from taskpilot.devices import DeviceConnection, DeviceHub  # noqa: E402
from taskpilot.model import ModelRuntime  # noqa: E402
from taskpilot.orchestrator import Config, Orchestrator  # noqa: E402
from taskpilot.policy import Policy  # noqa: E402
from taskpilot.store import Store  # noqa: E402

MARKER = "TaskPilot live test 12345"
POLICY = Policy.from_dict({"approved_apps": {"notepad": r"C:\Windows\System32\notepad.exe"}})


def get_clipboard() -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                       capture_output=True, text=True)
    return r.stdout.rstrip("\r\n")


def set_clipboard(text: str) -> None:
    if text:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                       input=text, text=True)
    else:   # it was empty (or non-text): leave it empty rather than keeping our marker
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.Clipboard]::Clear()"])


def foreground_rect():
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


class Loopback(DeviceConnection):
    def __init__(self, ex): self.ex = ex
    async def execute(self, req, timeout): return await self.ex.handle(req)
    async def query(self, action_id, timeout=5.0): return self.ex.lookup(action_id)
    async def cancel(self, task_id): self.ex.cancel_task(task_id)
    async def estop(self): self.ex.estop()


class GuardedScript(ModelRuntime):
    """Plays a fixed script but re-checks, before every input, that Notepad has focus."""

    def __init__(self, backend: WindowsBackend):
        self.b, self.stage, self.waits = backend, 0, 0
        self.report: dict = {}
        self.expected_click = None

    def notepad_focused(self) -> bool:
        w = self.b.active_window()
        return bool(w) and ("notepad" in w.title.lower() or "notepad" in w.process.lower())

    def abort(self, why):
        w = self.b.active_window()
        self.report['foreground_at_abort'] = (w.title, w.process) if w else None
        self.report["aborted"] = why
        return {"outcome": {"outcome": "cannot_proceed", "message": why}}

    @staticmethod
    def act(tool, **p): return {"action": {"tool": tool, **p}}

    async def propose(self, ctx):
        s = self.stage
        if s == 0:
            self.stage = 1
            return self.act("launch_application", app_id="notepad")
        if s <= 6 and not self.notepad_focused():
            if s == 1 and self.waits < 10:
                self.waits += 1
                return self.act("wait", seconds=1)
            return self.abort("Notepad does not have focus; refusing to send input")
        if s == 1:                                    # click the middle of the Notepad window
            l, t, r, b = foreground_rect()
            cx, cy = (l + r) // 2, (t + b) // 2
            dw, _ = self.b.display_size()
            scale = dw / ctx.screenshot_size[0]
            self.expected_click = (cx, cy)
            self.report["window_rect"] = (l, t, r, b)
            self.report["click_target_display"] = (cx, cy)
            self.stage = 2
            return self.act("click", x=round(cx / scale), y=round(cy / scale))
        if s == 2:
            import pyautogui
            px, py = pyautogui.position()
            ex, ey = self.expected_click
            self.report["mouse_after_click"] = (px, py)
            self.report["click_error_px"] = max(abs(px - ex), abs(py - ey))
            self.report["rect_now"] = foreground_rect()
            self.stage = 3
            return self.act("type_text", text=MARKER)
        if s == 3:
            self.stage = 4; return self.act("hotkey", keys=["ctrl", "a"])
        if s == 4:
            self.stage = 5; return self.act("hotkey", keys=["ctrl", "c"])
        if s == 5:
            await asyncio.sleep(0.5)
            clip = get_clipboard()
            self.report["clipboard_has_marker"] = MARKER in clip
            self.stage = 6
            return self.act("press_key", key="delete")          # clear what we typed
        if s == 6:
            self.stage = 7
            return self.act("hotkey", keys=["ctrl", "w"])        # policy: needs approval
        if s == 7:
            ok = self.report.get("clipboard_has_marker") and self.report.get("click_error_px", 99) <= 3
            if not ok:
                return self.abort(f"checks failed: {self.report}")
            return {"outcome": {"outcome": "done", "message": "typed, read back and cleaned up",
                                "evidence": f"clipboard contained '{MARKER}'; click landed within "
                                            f"{self.report['click_error_px']}px of target"}}


async def main() -> int:
    if any(p.name().lower() == "notepad.exe" for p in psutil.process_iter()):
        print("ABORT: Notepad is already open - close it first so we cannot touch your text.")
        return 2
    saved_clip = get_clipboard()
    backend = WindowsBackend()
    ex = Executor(backend, POLICY)
    hub, store = DeviceHub(), Store()
    store.enroll_device("local")
    hub.register("local", Loopback(ex))
    script = GuardedScript(backend)
    orch = Orchestrator(store, hub, script, POLICY,
                        Config(settle_delay_s=0.6, ready_timeout_s=15, action_timeout_s=20, confirm_timeout_s=30))
    print("Starting in 3 s - hands off mouse/keyboard (mouse to a corner = abort)...", flush=True)
    await asyncio.sleep(3)
    tid = orch.submit("local", "live test", max_actions=20, timeout_s=120)

    async def auto_approve():                      # exercises the confirmation flow
        while True:
            await asyncio.sleep(0.2)
            if orch.pending_approval(tid):
                orch.approve(tid, True)
                return
    approver = asyncio.create_task(auto_approve())
    try:
        task = await asyncio.wait_for(orch.wait(tid), 150)
    finally:
        approver.cancel()
        for p in psutil.process_iter():            # we launched it; nothing of the user's was open
            if p.name().lower() == "notepad.exe":
                p.kill()
                print("(cleanup) killed leftover notepad.exe", p.pid)
        set_clipboard(saved_clip)

    print("\n=== RESULT ===")
    print("task status :", task["status"], "|", task.get("result") or task.get("error"))
    for a in store.list_actions(tid):
        print(f"  #{a['seq']:<2} {a['tool']:<20} {a['status']:<10} {a['verification'] or a['error'] or ''}")
    print("report      :", script.report)
    print("transitions :", " -> ".join(t["to_state"] for t in store.transitions(tid)))
    for n in ("model_latency", "screenshot_latency", "action_duration"):
        v = [m["value_ms"] for m in store.metrics(n)]
        if v:
            print(f"{n:<19}: n={len(v)} avg={sum(v)/len(v):.0f}ms max={max(v):.0f}ms")
    return 0 if task["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

