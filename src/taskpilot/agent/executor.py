"""Agent-side executor: local policy re-check, idempotency, cancellation, e-stop."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from ..policy import Policy
from ..protocol import ActionRequest, ActionResult, GUI_TOOLS, Screenshot
from .backend import Backend
from .gate import window_access, window_allowed
from .polite import Politeness, needs_foreground
from ..tools import ToolContext, get_registry
from ..tools.registry import NeedsAccess
from .runner import ManagedProcesses, run_command

PRIMITIVES = ["capture_screen", "get_active_window", "launch_application", "click",
              "double_click", "type_text", "press_key", "hotkey", "scroll", "wait",
              "run_command", "get_process_status"]


def capabilities() -> list[str]:
    return PRIMITIVES + get_registry().names()


CAPABILITIES = capabilities()


class Executor:
    def __init__(self, backend: Backend, policy: Policy, cache_size: int = 512):
        self.backend, self.policy = backend, policy
        self.managed = ManagedProcesses()
        self._results: OrderedDict[str, ActionResult] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._task_actions: dict[str, set[str]] = {}
        self._cache_size = cache_size
        self.estopped = False
        self.persist = None                      # callable(kind, key, value) saving an "always" grant on this computer
        self._task_grants: dict[str, list[tuple[str, str]]] = {}      # task id -> [(kind, key)] to revoke at the end
        # manners: wait for the owner to pause before taking the mouse/keyboard/front window; put their window back after
        self.polite = Politeness(enabled=policy.polite, idle_s=policy.polite_idle_s, max_wait_s=policy.polite_max_wait_s,
                                 idle_source=getattr(backend, "user_idle_s", None))
        try:
            backend.polite = self.polite
        except Exception:
            pass

    # ------------------------------------------------------------ control
    def lookup(self, action_id: str) -> ActionResult | None:
        return self._results.get(action_id)

    def cancel_task(self, task_id: str) -> None:
        for aid in list(self._task_actions.get(task_id, ())):
            if (t := self._inflight.get(aid)) and not t.done():
                t.cancel()
        self.managed.kill_task(task_id)

    def end_task(self, task_id: str) -> None:
        """A task finished: take back every 'just this time' grant it was given."""
        for kind, key in self._task_grants.pop(task_id, []):
            self.policy.revoke(kind, key)
        try:
            self.polite.restore(task_id, self.backend.restore_user_context)
        except Exception:
            pass

    async def _grant(self, req, a, result):
        """Apply an access grant the USER approved in the app. Protected things are refused by policy.check_grant."""
        value = None
        if a.kind == "app":
            value = await asyncio.to_thread(self.backend.resolve_app, a.target)
            if value is None:
                return result("error", error=f"could not find an app called '{a.target}' on this computer")
        key = self.policy.grant(a.kind, a.target, value)
        if a.remember and self.persist:
            try:
                self.persist(a.kind, key, value)
            except Exception:
                pass                                     # still granted for this session; just not saved
        elif not a.remember:
            self._task_grants.setdefault(req.task_id, []).append((a.kind, key))
        return result(data={"granted": a.kind, "target": key, "remembered": bool(a.remember and self.persist)})

    def estop(self) -> None:
        self.estopped = True
        for t in list(self._inflight.values()):
            t.cancel()
        self.managed.kill_all()

    def reset_estop(self) -> None:
        self.estopped = False

    # ------------------------------------------------------------ execute
    async def handle(self, req: ActionRequest) -> ActionResult:
        # Duplicate delivery (e.g. after reconnect): never re-execute.
        if (cached := self._results.get(req.action_id)) is not None:
            return cached
        if (t := self._inflight.get(req.action_id)) is not None:
            return await asyncio.shield(t)
        task = asyncio.ensure_future(self._execute(req))
        self._inflight[req.action_id] = task
        self._task_actions.setdefault(req.task_id, set()).add(req.action_id)
        try:
            res = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                res = ActionResult(action_id=req.action_id, status="cancelled", error="cancelled")
            else:
                raise
        finally:
            self._inflight.pop(req.action_id, None)
            self._task_actions.get(req.task_id, set()).discard(req.action_id)
        self._results[req.action_id] = res
        while len(self._results) > self._cache_size:
            self._results.popitem(last=False)
        return res

    async def _execute(self, req: ActionRequest) -> ActionResult:
        a, started = req.action, time.time()

        def result(status="ok", **kw) -> ActionResult:
            return ActionResult(action_id=req.action_id, status=status, started_at=started,
                                finished_at=time.time(), **kw)

        if self.estopped:
            return result("denied", error="emergency stop active")
        d = self.policy.evaluate(a)       # server-side checks are repeated here, independently
        if d.verdict == "deny":
            return result("denied", error=d.reason, data={"needs_access": d.access} if d.access else {})
        if a.tool == "grant_access":
            return await self._grant(req, a, result)
        # 'confirm' was resolved by the server before dispatch; the agent trusts only the channel.
        b = self.backend
        if self.policy.restrict_input_to_approved and a.tool in GUI_TOOLS and a.tool != "launch_application":
            w = await asyncio.to_thread(b.active_window)       # input only ever goes to an approved app
            gate = await window_allowed(self.policy, b, w.title if w else "", w.process if w else "")
            if not gate.allowed:
                acc = await window_access(self.policy, b, w.title if w else "", w.process if w else "", gate)
                return result("denied", error=f"input blocked: {gate.reason}", data={"needs_access": acc} if acc else {})
        busy = False
        if self.policy.polite and needs_foreground(a):             # about to take the mouse, keyboard or front window
            self.polite.current_task = req.task_id
            if not await asyncio.to_thread(self.polite.wait_until_idle, req.task_id):
                return result("error", error="You are using the computer right now, so Deskhand waited and did not take over. "
                                             "It will try this step again when you pause.")
            await asyncio.to_thread(self.polite.remember, req.task_id, self.backend.capture_user_context)
            busy = True
        try:
            return await self._run_step(req, a, b, result)
        finally:
            if busy:
                self.polite.mark_own_input()

    async def _run_step(self, req, a, b, result):
        try:
            t = a.tool
            if t == "app_tool":
                tool = get_registry().get(a.name)
                if tool is None or tool.handler is None:      # recipes are expanded by the server
                    return result("denied", error=f"unknown tool '{a.name}'")
                args = tool.params.model_validate(a.args)
                # Acting on a control by name needs no focus, but some apps pull themselves to the front when touched. Note what
                # the person had in front, and put it back if the app took over.
                keep = (await asyncio.to_thread(b.capture_user_context)
                        if self.policy.polite and a.name.startswith("ui.") and a.name not in ("ui.snapshot", "ui.read", "ui.read_page", "ui.wait")
                        else None)
                try:
                    data = await tool.handler(ToolContext(b, self.policy, self.managed, req.task_id), args)
                finally:
                    if keep:
                        now = await asyncio.to_thread(b.capture_user_context)
                        if now and now.get("hwnd") != keep.get("hwnd") and self.polite.user_idle_s() >= 0.0:
                            try:
                                await asyncio.to_thread(b.restore_user_context, {"hwnd": keep.get("hwnd")})
                            except Exception:
                                pass
                shot = data.pop("_screenshot", None)
                if data.get("error"):
                    return result("error", error=str(data["error"]), data=data)
                return result(data=data, screenshot=Screenshot.model_validate(shot) if shot else None)
            if t == "capture_screen":
                if self.policy.cloud_safe:               # privacy mode: ONLY an approved foreground window
                    w = await asyncio.to_thread(b.active_window)
                    gate = await window_allowed(self.policy, b, w.title if w else "", w.process if w else "")
                    if not gate.allowed:
                        # Something else is in front (the person's own work). If an approved window exists, capture THAT window
                        # directly, even while others cover it: nothing is brought forward and nothing else is in the picture.
                        cands = []
                        try:
                            for x in await asyncio.to_thread(b.list_windows):
                                if (await window_allowed(self.policy, b, x["title"], x["process"])).allowed:
                                    cands.append(x)
                        except Exception:
                            cands = []
                        for x in cands:
                            try:
                                return result(screenshot=await asyncio.to_thread(b.screenshot_window_titled, x["title"], a.max_dimension))
                            except NotImplementedError:
                                break                                  # this backend cannot: fall back to bringing it forward
                            except Exception:
                                continue
                        if cands and not (self.policy.polite and self.polite.user_idle_s() < self.polite.idle_s):
                            try:                                       # nobody is using the computer: bringing it forward harms no one
                                await asyncio.to_thread(b.focus_window, cands[0]["title"])
                                w = await asyncio.to_thread(b.active_window)
                                gate = await window_allowed(self.policy, b, w.title if w else "", w.process if w else "")
                            except Exception:
                                pass
                    if not gate.allowed:
                        acc = await window_access(self.policy, b, w.title if w else "", w.process if w else "", gate)
                        return result("denied", error=f"screenshot not taken (privacy mode): {gate.reason}",
                                      data={"needs_access": acc} if acc else {})
                    return result(screenshot=await asyncio.to_thread(b.screenshot_window, a.max_dimension))
                return result(screenshot=await asyncio.to_thread(b.screenshot, a.max_dimension))
            if t == "get_active_window":
                w = await asyncio.to_thread(b.active_window)
                if self.policy.cloud_safe and w and not (await window_allowed(self.policy, b, w.title, w.process)).allowed:
                    return result(data={"window": None, "note": "the foreground window is not an approved application"})
                return result(data={"window": w.model_dump() if w else None})
            if t == "launch_application":
                pid = await asyncio.to_thread(b.launch, self.policy.approved_apps[a.app_id])
                return result(data={"pid": pid})
            if t == "get_process_status":
                target = self.policy.approved_apps.get(a.app_id) if a.app_id else None
                if a.app_id and target is None:
                    return result("denied", error=f"application '{a.app_id}' is not approved")
                return result(data=await asyncio.to_thread(b.process_status, target, a.pid, a.app_id))
            if t in ("click", "double_click"):
                w, h = await asyncio.to_thread(b.display_size)
                if not (0 <= a.x < w and 0 <= a.y < h):
                    return result("error", error=f"({a.x},{a.y}) outside {w}x{h} display")
                await asyncio.to_thread(b.click, a.x, a.y, getattr(a, "button", "left"),
                                        2 if t == "double_click" else 1)
            elif t == "scroll":
                await asyncio.to_thread(b.scroll, a.x, a.y, a.clicks)
            elif t == "type_text":
                await asyncio.to_thread(b.type_text, a.text)
            elif t == "press_key":
                await asyncio.to_thread(b.press, [a.key.lower()])
            elif t == "hotkey":
                await asyncio.to_thread(b.press, [k.lower() for k in a.keys])
            elif t == "wait":
                await asyncio.sleep(a.seconds)
            elif t == "run_command":
                data = await run_command(self.managed, req.task_id, a.executable, a.args, a.cwd,
                                         a.timeout_s, a.max_output_bytes)
                if data.get("error"):
                    return result("error", error=data["error"], data=data)
                return result(data=data)
            return result()
        except asyncio.CancelledError:
            raise
        except NeedsAccess as e:
            return result("denied", error=e.reason, data={"needs_access": e.access})
        except Exception as e:
            return result("error", error=f"{type(e).__name__}: {e}")
