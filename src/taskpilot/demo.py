"""Demo mode: a simulated laptop + a scripted 'model', so the app can be tried (and tested) with no real desktop,
no API key and no risk. Nothing here touches the real machine."""
from __future__ import annotations

import time

from .agent.backend import Backend
from .agent.executor import Executor
from .devices import DeviceConnection
from .model import ModelContext, ModelRuntime
from .policy import Policy
from .protocol import ActionRequest, ActionResult, Screenshot, WindowInfo

DEMO_DEVICE = "demo-laptop"
DEMO_WINDOW = {"title": "Demo Chat", "process": "demo.exe", "pid": 1, "handle": 1}


def demo_policy() -> Policy:
    return Policy.from_dict({"ui_allowed_windows": ["Demo"], "cloud_safe": True})


class DemoBackend(Backend):
    def __init__(self) -> None:
        self.typed = ""

    def display_size(self): return (1280, 720)

    def screenshot(self, max_dimension):
        return Screenshot(image_b64="", width=1280, height=720, display_width=1280, display_height=720, scale=1.0,
                          captured_at=time.time(), active_window=WindowInfo(title="Demo Chat", process="demo.exe"))

    def screenshot_window(self, max_dimension): return self.screenshot(max_dimension)
    def active_window(self): return WindowInfo(title="Demo Chat", process="demo.exe", pid=1)
    def launch(self, target): return 1
    def process_status(self, target, pid, hint=None): return {"running": True, "pid": 1, "window_visible": True}
    def click(self, *a, **k): pass
    def type_text(self, text): pass
    def press(self, keys): pass
    def scroll(self, *a, **k): pass
    def list_windows(self): return [{"title": "Demo Chat", "process": "demo.exe", "pid": 1}]
    def focus_window(self, title): return {"focused": True, "active_title": "Demo Chat"}
    def ui_window(self, title): return dict(DEMO_WINDOW) if title.lower() in ("", "demo chat", "demo") else None

    def ui_snapshot(self, win, max_elements, query, name_chars=90):
        tree = 'Edit "Message" id=msg\nButton "Send" id=send\nText "Demo conversation" id=title'
        return {"tree": tree, "count": 3, "shown": 3, "truncated": False,
                "window": {"title": win["title"], "process": win["process"]}, "hint": ""}

    def ui_act(self, win, sel, action, text):
        if action == "type":
            self.typed = text or ""
            return {"method": "value", "element": 'Edit "Message"', "readback_available": True, "value_after": self.typed}
        if action == "read":
            return {"text": self.typed or "Demo conversation", "source": "name", "element": 'Text "Demo conversation"'}
        return {"method": "invoke", "element": f'Button "{sel.get("name") or sel.get("id")}"'}

    def ui_read_page(self, win, min_wait_s, quiet_s, max_wait_s, max_chars):
        text = "This is demo mode. On a real device this would be the text of the page Pilot is looking at."
        return {"page_text": text, "chars": len(text), "truncated": False, "stable": True,
                "still_generating": False, "waited_s": 0.0}


class DemoConnection(DeviceConnection):
    def __init__(self, policy: Policy):
        self.ex = Executor(DemoBackend(), policy)

    async def execute(self, req: ActionRequest, timeout: float) -> ActionResult: return await self.ex.handle(req)
    async def query(self, action_id, timeout=5.0): return self.ex.lookup(action_id)
    async def cancel(self, task_id): self.ex.cancel_task(task_id)
    async def estop(self): self.ex.estop()


class DemoModel(ModelRuntime):
    """Looks around, then (for goals about sending/messaging) types and clicks Send, which needs approval."""

    async def propose(self, ctx: ModelContext):
        goal, n = ctx.goal.lower(), len(ctx.history)
        if any(h.get("status") == "denied" for h in ctx.history):
            return {"outcome": {"outcome": "cannot_proceed", "message": "(demo) You declined, so I stopped."}}
        act = lambda tool, **p: {"reasoning": "demo", "action": {"tool": tool, **p}}
        messaging = any(w in goal for w in ("send", "message", "reply", "text "))
        plan = [act("windows.list"), act("ui.snapshot", window="Demo")]
        plan += ([act("ui.type", window="Demo", name="Message", text=ctx.goal[:80]),
                  act("ui.click", window="Demo", name="Send")] if messaging else [act("ui.read_page", window="Demo")])
        if n < len(plan):
            return plan[n]
        return {"outcome": {"outcome": "done", "evidence": "all demo steps completed",
                            "message": f"(demo mode) I would carry out: {ctx.goal}. "
                                       "This is a simulation, so nothing on your computers changed."}}
