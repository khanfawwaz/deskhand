import asyncio
import sys

import pytest

from taskpilot.agent.backend import Backend
from taskpilot.agent.executor import Executor
from taskpilot.devices import ActionTimeout, DeviceConnection, DeviceHub, DeviceUnavailable
from taskpilot.model import ScriptedRuntime
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.policy import Policy
from taskpilot.protocol import ActionRequest, ActionResult, Screenshot, WindowInfo
from taskpilot.store import Store


class FakeUI:
    """In-memory UI Automation world: a working Calculator, a form (duplicate 'Save' buttons, a password
    field) and a protected shell window. Lets us test ui.* tools and recipe replay without a real desktop."""
    DIGITS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]

    def __init__(self):
        self.reset()
        self.windows = {
            "Calculator": {"process": "ApplicationFrameHost.exe", "pid": 100},
            "Contact form": {"process": "formapp.exe", "pid": 101},
            "Command Prompt": {"process": "cmd.exe", "pid": 102},
            "Other app": {"process": "other.exe", "pid": 103},
            "Canvas app": {"process": "canvas.exe", "pid": 104},     # custom-drawn: exposes no controls at all
        }
        self.foreground = "Calculator"
        self.hidden: set[str] = set()          # control names an "app update" removed
        self.calc_bug = False                  # simulate a calculator that computes wrongly

    def reset(self):
        self.acc, self.op, self.cur, self.fresh = None, None, "0", True
        self.name_value, self.password_value, self.subscribed = "", "", False

    def elements(self, title: str) -> list[dict]:
        def el(role, name, id_="", value=None, state=()):
            return {"role": role, "name": name, "id": id_, "value": value, "state": list(state), "enabled": True}
        if title == "Calculator":
            els = [el("Button", n, f"num{i}Button") for i, n in enumerate(self.DIGITS)]
            els += [el("Button", "Plus", "plusButton"), el("Button", "Minus", "minusButton"),
                    el("Button", "Equals", "equalButton"), el("Button", "Clear", "clearButton"),
                    el("Text", f"Display is {self.cur}", "CalculatorResults")]
            return [e for e in els if e["name"] not in self.hidden]
        if title == "Contact form":
            return [el("Edit", "Name", "nameBox", self.name_value),
                    el("Edit", "Password", "pwBox", "*" * len(self.password_value), ["password"]),
                    el("Button", "Save", "saveTop"), el("Button", "Save", "saveBottom"),
                    el("CheckBox", "Subscribe", "subBox", None, ["checked"] if self.subscribed else []),
                    # a web-style link: the same words exist as plain text AND as the link around them
                    el("Text", "Privacy policy", ""), el("Hyperlink", "Privacy policy", "policyLink"),
                    el("Text", "Privacy policy", "")]
        if title == "Canvas app":
            return []
        return [el("Button", "OK", "okButton")]

    def press(self, name: str):
        if name in self.DIGITS:
            d = str(self.DIGITS.index(name))
            self.cur = d if self.fresh or self.cur == "0" else self.cur + d
            self.fresh = False
        elif name in ("Plus", "Minus"):
            self.acc, self.op, self.fresh = int(self.cur), name, True
        elif name == "Equals" and self.op:
            b = int(self.cur)
            r = self.acc + b if self.op == "Plus" else self.acc - b
            self.cur, self.op, self.fresh = str(r + (1 if self.calc_bug else 0)), None, True
        elif name == "Clear":
            self.acc, self.op, self.cur, self.fresh = None, None, "0", True


class FakeBackend(Backend):
    def __init__(self):
        self.calls: list[tuple] = []
        self.running = False
        self.launch_works = True
        self.windows = [{"title": "Untitled - Notepad", "process": "Notepad.exe", "pid": 11}]
        self.active = "Desktop"
        self.clipboard = ""
        self.clipboard_breaks = False   # simulate a clipboard that silently drops writes
        self.ui = FakeUI()
        self.zooms: list[tuple] = []
        self.hosts: dict[str, str] = {}             # window title -> host shown in its address bar
        self.apps = {"spotify": r"shell:AppsFolder\SpotifyAB.SpotifyMusic!Spotify"}      # what resolve_app can find
        self.active_process = ""                    # process of the foreground window (real Windows always has one)

    def display_size(self): return (2560, 1440)

    def screenshot(self, max_dimension):
        return Screenshot(image_b64="AAAA", width=1280, height=720, display_width=2560,
                          display_height=1440, scale=2.0, captured_at=0,
                          active_window=WindowInfo(title=self.active))

    def active_window(self): return WindowInfo(title=self.active, process=self.active_process)

    def list_windows(self): return list(self.windows)

    def focus_window(self, title):
        self.calls.append(("focus", title))
        hit = next((w for w in self.windows if title.lower() in w["title"].lower()), None)
        if hit:
            self.active = hit["title"]
        return {"focused": bool(hit), "active_title": self.active}

    def get_clipboard(self): return self.clipboard

    def set_clipboard(self, text):
        self.calls.append(("clip", text))
        if not self.clipboard_breaks:
            self.clipboard = text

    def open_url(self, url): self.calls.append(("url", url))

    # ---- UI Automation fake (shares the REAL matching code so selector semantics are tested too)
    def ui_window(self, title):
        names = list(self.ui.windows)
        t = (title or self.ui.foreground).lower()
        hit = next((n for n in names if t in n.lower()), None)
        return None if hit is None else {"title": hit, **self.ui.windows[hit], "handle": 1}

    @staticmethod
    def _line(e, nth=None):
        parts = [f'{e["role"]} "{e["name"]}"', f"id={e['id']}"] if e["id"] else [f'{e["role"]} "{e["name"]}"']
        if e["value"] is not None:
            parts.append(f'value="{e["value"]}"')
        if nth:
            parts.append(f"#{nth}")
        return " ".join(parts + [f"[{s}]" for s in e["state"]])

    def ui_read_page(self, win, min_wait_s, quiet_s, max_wait_s, max_chars):
        self.calls.append(("read_page", win["title"]))
        text = getattr(self, "page_text", "The best cheap pick is the DS3225 at about Rs 1,200; power it from a separate 6-7V supply.")
        return {"page_text": text[-max_chars:], "chars": len(text), "truncated": len(text) > max_chars,
                "stable": True, "still_generating": False, "waited_s": 0.0}

    def screenshot_window(self, max_dimension):
        self.calls.append(("window_shot",))
        return Screenshot(image_b64="WIN", width=640, height=360, display_width=2560, display_height=1440,
                          scale=2.0, origin_x=100, origin_y=50, captured_at=0, active_window=WindowInfo(title=self.active))

    def window_host(self, title):
        return self.hosts.get(title)

    def resolve_app(self, name):
        return self.apps.get(name.lower())

    def open_private(self, url):
        self.calls.append(("private_url", url))
        return {"browser": "chrome", "private": True}

    def ui_snapshot(self, win, max_elements, query, name_chars=90):
        els = [e for e in self.ui.elements(win["title"]) if query.lower() in (e["name"] + e["id"]).lower()]
        shown = els[:max_elements]
        return {"tree": "\n".join(self._line(e) for e in shown), "count": len(els), "shown": len(shown),
                "truncated": len(els) > len(shown), "window": {"title": win["title"], "process": win["process"]},
                "hint": ""}

    def ui_act(self, win, sel, action, text):
        from taskpilot.agent import uia
        if win["title"] not in self.ui.windows:
            raise LookupError("window closed")
        cands = uia._matches(self.ui.elements(win["title"]), sel)
        el, err = uia.choose(cands, sel, action, describe=self._line)     # the REAL selection logic
        if err:
            return err
        d = self._line(el)
        if action == "read":
            if "password" in el["state"]:
                raise PermissionError("password field: refusing to read it")
            return {"text": el["value"] if el["value"] is not None else el["name"],
                    "source": "value" if el["value"] is not None else "name", "element": d}
        if action in ("click", "double_click"):
            self.calls.append(("ui_click", win["title"], el["name"], el["id"], el["role"]))
            if win["title"] == "Calculator":
                self.ui.press(el["name"])
            if el["role"] == "CheckBox":
                self.ui.subscribed = not self.ui.subscribed
            return {"method": "invoke", "element": d}
        if action == "type":
            if "password" in el["state"]:
                return {"error": "password field: refusing to type into it"}
            self.calls.append(("ui_type", win["title"], el["name"]))
            self.ui.name_value = text
            return {"method": "value", "element": d, "readback_available": True, "value_after": self.ui.name_value}
        return {"error": f"unknown action '{action}'"}

    def ui_wait(self, win, sel, timeout_s, gone):
        from taskpilot.agent import uia
        present = bool(uia._matches(self.ui.elements(win["title"]), sel))
        if present != gone:
            return {"met": True, "waited_s": 0.0}
        return {"error": f"timed out after {timeout_s}s waiting for {uia._sel_text(sel)}"}

    def screenshot_region(self, x, y, w, h, max_dimension, upscale=True):
        self.zooms.append((x, y, w, h))
        return Screenshot(image_b64="ZOOM", width=w * 2, height=h * 2, display_width=2560, display_height=1440,
                          scale=0.5, origin_x=x, origin_y=y, captured_at=0, active_window=WindowInfo(title="Desktop"))

    def launch_cli(self, exe, args):
        self.calls.append(("cli", exe, tuple(args)))
        return 99

    def launch(self, target):
        self.calls.append(("launch", target))
        self.running = self.launch_works
        return 4242

    def process_status(self, target, pid, hint=None):
        if not self.running and hint and any(hint.lower() in t.lower() for t in self.ui.windows):
            return {"running": True, "pid": 100, "window_visible": True, "title": "Calculator"}   # Store app found by title
        return {"running": self.running, "pid": 4242 if self.running else None,
                "window_visible": self.running, "title": "Untitled - Notepad" if self.running else ""}

    def click(self, x, y, button, clicks): self.calls.append(("click", x, y, button, clicks))
    def type_text(self, text): self.calls.append(("type", text))
    def press(self, keys): self.calls.append(("press", tuple(keys)))
    def scroll(self, x, y, clicks): self.calls.append(("scroll", x, y, clicks))


class Loopback(DeviceConnection):
    """In-process stand-in for the WebSocket link with failure injection."""

    def __init__(self, executor: Executor):
        self.ex = executor
        self.fail_before = 0      # raise Timeout without executing
        self.fail_after = 0       # execute, then lose the reply (outcome unknown to server)
        self.query_down = False
        self.cancelled: list[str] = []
        self.delay = 0.0

    async def execute(self, req: ActionRequest, timeout: float) -> ActionResult:
        if req.action.tool != "capture_screen":
            if self.fail_before > 0:
                self.fail_before -= 1
                raise ActionTimeout("injected")
            if self.fail_after > 0:
                self.fail_after -= 1
                await self.ex.handle(req)
                raise ActionTimeout("injected after execute")
        if self.delay:
            await asyncio.sleep(self.delay)
        return await self.ex.handle(req)

    async def query(self, action_id, timeout=5.0):
        if self.query_down:
            raise DeviceUnavailable("down")
        return self.ex.lookup(action_id)

    async def cancel(self, task_id):
        self.cancelled.append(task_id)
        self.ex.cancel_task(task_id)

    async def estop(self): self.ex.estop()
    async def end_task(self, task_id): self.ex.end_task(task_id)


@pytest.fixture(autouse=True)
def _no_leaked_recipe_tools():
    """Recipe tools live in the process-wide registry: never let one test's recipes reach another."""
    yield
    from taskpilot.tools import get_registry
    for n in [n for n in get_registry().names() if n.startswith("recipe.")]:
        get_registry().unregister(n)


def make_policy(tmp_path) -> Policy:
    return Policy.from_dict({
        "approved_apps": {"notepad": r"C:\Windows\System32\notepad.exe"},
        "ui_allowed_windows": ["Calculator", "Contact form"],
        "allowed_dirs": [str(tmp_path)],
        "command_rules": [
            {"executable": "git", "allowed_subcommands": ["status", "log"]},
            {"executable": "python"},
            {"executable": "whoami"},
        ],
    })


class Env:
    def __init__(self, tmp_path, script, **cfg_over):
        self.policy = make_policy(tmp_path)
        self.backend = FakeBackend()
        self.executor = Executor(self.backend, self.policy)
        self.conn = Loopback(self.executor)
        self.hub = DeviceHub()
        self.store = Store()
        self.store.enroll_device("dev1")
        self.hub.register("dev1", self.conn)
        self.model = ScriptedRuntime(script)
        cfg = Config(**{**dict(settle_delay_s=0, ready_poll_s=0.01, ready_timeout_s=0.2,
                               device_wait_s=0.5, confirm_timeout_s=2, cancel_grace_s=0.2,
                               action_timeout_s=2, screenshot_mode="always", prefer_ui=False), **cfg_over})
        self.orch = Orchestrator(self.store, self.hub, self.model, self.policy, cfg)
        self.access_choice, self.question_reply, self.asked = "no", None, []

    async def _answerer(self, tid):
        """Stands in for the user: answers access requests with `access_choice` (default: decline, i.e. the old
        'not approved = refused' behaviour) and questions with `question_reply` (default: leave them pending)."""
        while True:
            await asyncio.sleep(0.01)
            p = self.orch.pending_approval(tid)
            if not p:
                continue
            if p.get("kind") == "access":
                self.asked.append(p)
                self.orch.answer(tid, self.access_choice)
            elif p.get("kind") == "question" and self.question_reply is not None:
                self.asked.append(p)
                self.orch.answer(tid, text=self.question_reply)

    async def run(self, goal="do it", **kw):
        tid = self.orch.submit("dev1", goal, **kw)
        helper = asyncio.ensure_future(self._answerer(tid))
        try:
            t = await asyncio.wait_for(self.orch.wait(tid), 10)
        finally:
            helper.cancel()
        return tid, t

    def tools(self, tid):
        return [(a["tool"], a["status"]) for a in self.store.list_actions(tid)]


@pytest.fixture
def make_env(tmp_path):
    return lambda script, **cfg: Env(tmp_path, script, **cfg)


def act(tool, **p): return {"reasoning": "r", "action": {"tool": tool, **p}}
def done(evidence="Notepad visible"): return {"outcome": {"outcome": "done", "message": "ok", "evidence": evidence}}
