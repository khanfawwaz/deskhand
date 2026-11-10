"""Sharing the computer with its owner.

Deskhand's own work should never fight with the person using the computer. This module is the "manners":

  * Before a step that needs the real mouse, keyboard or the front window, wait until the person has paused for a moment.
    If they start using the computer again while Deskhand is waiting, it keeps waiting. It never grabs the mouse mid-gesture.
  * Remember what the person had in front (and where their mouse was) before the first such step of a task, and put it
    back when the task ends, if they are not using the computer at that moment.
  * Tell the tray icon what it is doing ("working", "waiting for you to pause") so nothing is a surprise.

Steps that do not need the foreground (reading a page, clicking a button by its name, web.* tools in the agent's own browser
window, capturing a window that is behind others) are never delayed: that is how both can work at once.

Deskhand's own keystrokes count as input to Windows. `mark_own_input()` tells this module "that last input was mine", so it
is not mistaken for the person.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

NEEDS_FOREGROUND_PRIMITIVES = {"click", "double_click", "type_text", "press_key", "hotkey", "scroll", "launch_application"}
NEEDS_FOREGROUND_TOOLS = {"windows.focus", "browser.open_url", "browser.open_private", "clipboard.set", "vscode.open"}


def needs_foreground(action) -> bool:
    """Does this action take over the mouse, the keyboard or the front window?"""
    tool = getattr(action, "tool", "")
    if tool == "app_tool":
        return getattr(action, "name", "") in NEEDS_FOREGROUND_TOOLS
    return tool in NEEDS_FOREGROUND_PRIMITIVES


class Politeness:
    def __init__(self, enabled: bool = True, idle_s: float = 2.5, max_wait_s: float = 45.0, poll_s: float = 0.25, *,
                 idle_source: Callable[[], float | None] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 on_state: Callable[[str], None] | None = None):
        self.enabled, self.idle_s, self.max_wait_s, self.poll_s = enabled, idle_s, max_wait_s, poll_s
        self.idle_source = idle_source                       # seconds since the PERSON last used mouse/keyboard; None = unknown
        self.clock, self.sleep, self.on_state = clock, sleep, on_state
        self._own_at: float | None = None                    # when Deskhand itself last sent input
        self._lock = threading.Lock()
        self._contexts: dict[str, dict] = {}                 # task id -> what the person had in front before we touched anything
        self.waited_total = 0.0
        self.state = "idle"

    # ---------------------------------------------------------------- who is using the computer
    def mark_own_input(self) -> None:
        self._own_at = self.clock()

    def user_idle_s(self) -> float:
        """Seconds since the PERSON last used the mouse or keyboard (large when unknown)."""
        if self.idle_source is None:
            return 1e9
        raw = self.idle_source()
        if raw is None:
            return 1e9
        if self._own_at is not None:
            since_own = self.clock() - self._own_at
            if raw >= since_own - 0.30:                       # the latest input is ours (or within a blink after it)
                return since_own + self.idle_s                # we only act after a pause, so they were idle before it
        return float(raw)

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            if self.on_state:
                try:
                    self.on_state(state)
                except Exception:
                    pass

    # ---------------------------------------------------------------- waiting for a pause
    def wait_until_idle(self, task_id: str = "") -> bool:
        """Block until the person has paused for `idle_s`. False if they never did within `max_wait_s`."""
        if not self.enabled:
            return True
        start = self.clock()
        interrupted = False
        while True:
            if self.user_idle_s() >= self.idle_s:
                self.waited_total += self.clock() - start
                if interrupted:
                    self.forget(task_id)                      # they used the computer meanwhile: what we saved is out of date
                self._set("working")
                return True
            interrupted = True
            self._set("waiting")
            if self.clock() - start >= self.max_wait_s:
                self.waited_total += self.clock() - start
                self._set("idle")
                return False
            self.sleep(self.poll_s)

    # ---------------------------------------------------------------- put things back as they were
    def remember(self, task_id: str, capture: Callable[[], dict | None]) -> None:
        """Save what the person has in front, once per task, before the first step that will change it."""
        if not self.enabled or not task_id:
            return
        with self._lock:
            if task_id in self._contexts:
                return
        try:
            ctx = capture()
        except Exception:
            ctx = None
        if ctx:
            with self._lock:
                self._contexts[task_id] = ctx

    def forget(self, task_id: str) -> None:
        with self._lock:
            self._contexts.pop(task_id, None)

    def restore(self, task_id: str, restore: Callable[[dict], None]) -> bool:
        """The task ended: put the person's window and mouse back, unless they are using the computer right now."""
        with self._lock:
            ctx = self._contexts.pop(task_id, None)
        self._set("idle")
        if not ctx or not self.enabled:
            return False
        if self.user_idle_s() < min(self.idle_s, 1.5):
            return False                                      # they are busy: do not pull the window out from under them
        try:
            restore(ctx)
            self.mark_own_input()
            return True
        except Exception:
            return False
