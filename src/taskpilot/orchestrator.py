"""Deterministic orchestrator: observe -> reason -> validate -> act -> verify."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from .devices import ActionTimeout, DeviceConnection, DeviceHub, DeviceUnavailable
from .model import ModelContext, ModelRuntime, extract_json
from .grounding import grounding_score
from .memory import hints_for, summarize_run
from .normalize import normalize_proposal
from .playbooks import playbooks_for, requirements_for
from .policy import Policy, risk_screen
from .recipes import (
    bind_params, build_draft, recordable_step, render_step, step_action,
)
from .protocol import (
    COORD_TOOLS, GUI_TOOLS, NON_IDEMPOTENT, OBSERVATION_TOOLS, Action, ActionRequest, ActionResult,
    AppToolCall, CaptureScreen, GetProcessStatus, GrantAccess, Proposal, Screenshot, action_label,
)
from .states import TERMINAL, TaskState
from .store import Store
from .tools import get_registry

log = logging.getLogger("taskpilot.orchestrator")


@dataclass
class Config:
    max_invalid_proposals: int = 4     # consecutive malformed / policy-denied proposals
    confirm_timeout_s: float = 300
    device_wait_s: float = 30
    action_timeout_s: float = 30
    ready_timeout_s: float = 10        # app launch readiness polling
    ready_poll_s: float = 0.5
    settle_delay_s: float = 0.3
    max_dispatch_retries: int = 2
    screenshot_dim: int = 1280
    cancel_grace_s: float = 3
    # "auto": the model sees a screenshot only after a visual action or when it asks (capture_screen).
    # "always": a fresh screenshot every step (slow; the old behaviour).
    screenshot_mode: str = "auto"
    # Save a DRAFT recipe from each successful task that used replayable (ui.*) steps. Off by default: a draft
    # contains the literal text the run typed. A draft does nothing until a human approves it.
    record_recipes: bool = False
    # Pixel clicks are the least reliable way to drive an app. When True, click/double_click are refused until
    # the app has been probed with ui.snapshot, and stay refused for apps that expose controls (use ui.click).
    prefer_ui: bool = True
    max_page_opens: int = 5          # hard cap on browser.open_* per task: a confused model must not spam windows
    max_idle_steps: int = 10         # this many steps in a row that only look around (or are refused) = stalled
    ground_evidence_min: float = 0.4   # share of a "done" claim's distinctive terms that must appear in what was observed
    ground_message_min: float = 0.35   # same, for the answer in the final message of read-the-result tasks
    learn: bool = True               # keep a non-sensitive summary of verified successes as hints for similar goals
    question_timeout_s: float = 900  # how long to wait for the user to answer a question or an access request
    max_questions: int = 5           # questions the model may ask the user in one task


class _Stop(Exception):
    def __init__(self, state: TaskState, msg: str):
        self.state, self.msg = state, msg


class Orchestrator:
    def __init__(self, store: Store, hub: DeviceHub, model: ModelRuntime, policy: Policy,
                 config: Config | None = None):
        self.store, self.hub, self.model, self.policy = store, hub, model, policy
        self.cfg = config or Config()
        self._runs: dict[str, asyncio.Task] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._confirm: dict[str, asyncio.Future] = {}
        self._pending_action: dict[str, dict] = {}

    # ------------------------------------------------------------ public API
    def submit(self, device_id: str, goal: str, max_actions: int = 25,
               timeout_s: float = 300, permission_context: dict | None = None) -> str:
        if not self.store.device_known(device_id):
            raise ValueError(f"device '{device_id}' is not enrolled")
        tid = self.store.create_task(device_id, goal, max_actions, timeout_s, permission_context)
        self._cancel[tid] = asyncio.Event()
        self._runs[tid] = asyncio.create_task(self._run(tid), name=f"task-{tid}")
        return tid

    def submit_recipe(self, device_id: str, name: str, args: dict | None = None, timeout_s: float = 300) -> str:
        """Replay an approved recipe on a device with NO model involved."""
        if not self.store.device_known(device_id):
            raise ValueError(f"device '{device_id}' is not enrolled")
        tid = self.store.create_task(device_id, f"Run recipe {name}", 1, timeout_s, {"recipe": name})
        self._cancel[tid] = asyncio.Event()
        self._runs[tid] = asyncio.create_task(self._run(tid, recipe=(name, args or {})), name=f"task-{tid}")
        return tid

    async def wait(self, tid: str) -> dict:
        if (t := self._runs.get(tid)) is not None:
            await asyncio.shield(t)
        return self.store.get_task(tid)  # type: ignore[return-value]

    async def cancel(self, tid: str) -> bool:
        t = self.store.get_task(tid)
        if not t or TaskState(t["status"]) in TERMINAL:
            return False
        self._cancel[tid].set()
        if (fut := self._confirm.get(tid)) and not fut.done():
            fut.set_result(False)
        if conn := self.hub.get(t["device_id"]):
            try:
                await conn.cancel(tid)
            except Exception:
                pass
        return True

    def approve(self, tid: str, approved: bool = True) -> bool:
        fut = self._confirm.get(tid)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def answer(self, tid: str, choice: str | None = None, text: str | None = None) -> bool:
        """The user's reply to a question or an access request (a choice id and/or free text)."""
        fut = self._confirm.get(tid)
        if fut is None or fut.done():
            return False
        info = self._pending_action.get(tid) or {}
        valid = {o["id"] for o in info.get("options", [])}
        if choice is not None and valid and choice not in valid:
            return False
        if choice is None and not (text and text.strip()):
            return False
        fut.set_result({"choice": choice, "text": (text or "").strip()[:1000]})
        return True

    def pending_approval(self, tid: str) -> dict | None:
        return self._pending_action.get(tid)

    async def emergency_stop(self, workspace: str | None = None) -> None:
        """Stop everything, or (with `workspace`) only that workspace's tasks and laptops."""
        for t in self.store.list_tasks(500, workspace=workspace):
            if TaskState(t["status"]) not in TERMINAL:
                self._cancel.setdefault(t["id"], asyncio.Event()).set()
                if (fut := self._confirm.get(t["id"])) and not fut.done():
                    fut.set_result(False)         # a task waiting for approval must stop NOW, not after its timeout
        if workspace:
            await self.hub.estop_devices([d["device_id"] for d in self.store.list_devices(workspace)])
        else:
            await self.hub.estop_all()

    # ------------------------------------------------------------- main loop
    async def _run(self, tid: str, recipe: tuple[str, dict] | None = None) -> None:
        t0 = time.monotonic()
        task = self.store.get_task(tid)
        assert task
        deadline = t0 + task["timeout_s"]
        try:
            if recipe is not None:
                await self._recipe_task(tid, task, deadline, *recipe)
            else:
                await self._loop(tid, task, deadline)
        except _Stop as s:
            self.store.transition(tid, s.state, s.msg,
                                  result=s.msg if s.state == TaskState.COMPLETED else None,
                                  error=None if s.state in (TaskState.COMPLETED, TaskState.CANCELLED) else s.msg)
        except asyncio.CancelledError:
            self.store.transition(tid, TaskState.CANCELLED, "server shutdown")
            raise
        except Exception as e:  # defensive: never leave a task non-terminal
            log.exception("task %s crashed", tid)
            self.store.transition(tid, TaskState.FAILED, "internal error", error=f"internal error: {e!r}")
        finally:
            self.store.add_metric(tid, "task_total", (time.monotonic() - t0) * 1000)
            if conn := self.hub.get(task["device_id"]):
                try:
                    await asyncio.wait_for(conn.end_task(tid), 3)      # 'just this time' access ends with the task
                except Exception:
                    pass

    def _check(self, tid: str, deadline: float) -> None:
        if self._cancel[tid].is_set():
            raise _Stop(TaskState.CANCELLED, "cancelled by user")
        if time.monotonic() > deadline:
            raise _Stop(TaskState.FAILED, "task timeout exceeded")

    async def _loop(self, tid: str, task: dict, deadline: float) -> None:
        dev = task["device_id"]
        await self._ensure_device(tid, dev, deadline)
        self.store.transition(tid, TaskState.RUNNING, "device available")

        # Dangerous-sounding goals never start silently, whatever the model would have done.
        if (why := risk_screen(task["goal"])):
            if not await self._wait_confirm(tid, {"tool": "goal", "question": "Run this high-impact task?",
                                                  "reason": why}, deadline):
                raise _Stop(TaskState.CANCELLED if self._cancel[tid].is_set() else TaskState.FAILED,
                            f"high-impact goal not approved: {why}")

        history: list[dict] = []
        notes: list[str] = []
        seq = 0
        invalid = 0
        auto = self.cfg.screenshot_mode == "auto"
        want_shot = not auto                       # in auto mode the model asks for a screen when needed
        pending_shot: Screenshot | None = None     # a zoomed crop the model asked for, shown next step
        recorded: list[dict] = []                  # replayable steps of this run (for a draft recipe)
        ui_status: dict[str, str] = {}             # window title -> "good" | "poor" | "blocked" (from ui.snapshot)
        opened_urls: set[str] = set()              # pages already opened in this task (never open one twice)
        observed: list[str] = []                   # everything Pilot actually read: the ground truth for "done" claims
        attempts: dict[str, list[bool]] = {}       # exact action -> outcomes, to stop repeating what does not work
        exp_hints = self._experience_hints(task["goal"], task.get("workspace"))
        skipped = 0
        questions = 0
        access_state: dict[tuple, str] = {}        # (kind, target) -> "granted" | "no": never ask twice in one task

        while True:
            self._check(tid, deadline)
            idle = self._idle_streak(history)
            if idle >= self.cfg.max_idle_steps:
                raise _Stop(TaskState.FAILED, f"no progress: the last {idle} steps only looked around or were refused")
            # ---- observe (only when a screenshot is useful)
            self.store.transition(tid, TaskState.RUNNING, "observing")
            shot: Screenshot | None = None
            if pending_shot is not None:
                shot, pending_shot = pending_shot, None      # screen.zoom: show the crop, not a full capture
            elif want_shot:
                conn = await self._ensure_device(tid, dev, deadline)
                res = await self._observe(tid, conn, deadline)
                if res is not None and res.status == "denied":
                    notes.append(f"{res.error}. No approved window is open: open the page you need (once) with "
                                 "browser.open_private, or bring the right window forward with windows.focus. Do NOT "
                                 "open a page you already opened.")
                elif res is None or res.screenshot is None:
                    raise _Stop(TaskState.FAILED, f"screenshot failed: {res.error if res else 'no result'}")
                else:
                    shot = res.screenshot
                if auto:
                    want_shot = False

            # ---- reason
            if shot and shot.active_window and shot.active_window.title:
                observed.append(shot.active_window.title)
            ctx = ModelContext(
                goal=task["goal"], state=self.store.get_task(tid)["status"], history=history,
                screenshot_b64=shot.image_b64 if shot else None,
                screenshot_size=(shot.width, shot.height) if shot else None,
                active_window=(shot.active_window.title if shot and shot.active_window else ""),
                approved_apps=sorted(self.policy.approved_apps), notes=notes,
                playbook="\n".join(x for x in (playbooks_for(task["goal"]), exp_hints) if x))
            self.store.transition(tid, TaskState.WAITING_FOR_ACTION, "awaiting model proposal")
            m0 = time.monotonic()
            try:
                raw = await asyncio.wait_for(self.model.propose(ctx), max(1, deadline - m0))
            except asyncio.TimeoutError:
                raise _Stop(TaskState.FAILED, "task timeout exceeded during model inference")
            except Exception as e:
                raise _Stop(TaskState.FAILED, f"model runtime error: {e!r}")
            m_ms = (time.monotonic() - m0) * 1000
            self.store.add_metric(tid, "model_latency", m_ms)
            self._check(tid, deadline)

            # ---- validate
            proposal = self._parse(raw)
            if isinstance(proposal, str):
                invalid += 1
                notes.append(f"rejected: {proposal}")
                seq += 1
                self.store.record_action(uuid.uuid4().hex[:12], tid, seq, "invalid", {}, "denied",
                                         proposal, m_ms)
                if invalid >= self.cfg.max_invalid_proposals:
                    raise _Stop(TaskState.FAILED, f"too many invalid proposals: {proposal}")
                continue

            if proposal.outcome is not None:
                o = proposal.outcome
                if o.outcome == "done":
                    ok, why, detail = self._completion_gate(history, o.evidence, o.message, task["goal"], observed)
                    if not ok:
                        seq += 1                               # a rejected claim leaves an audit row (with its scores)
                        self.store.record_action(uuid.uuid4().hex[:12], tid, seq, "done", {}, "denied",
                                                 f"{why} [{detail}]", m_ms)
                        notes.append(f"done rejected: {why}")
                        invalid += 1
                        if invalid >= self.cfg.max_invalid_proposals:
                            raise _Stop(TaskState.FAILED, f"completion not verifiable: {why}")
                        continue
                    self._save_draft(tid, task, recorded, skipped)
                    self._learn(tid, task)
                    self.store.transition(tid, TaskState.VERIFYING, f"final verification passed ({detail})")
                    raise _Stop(TaskState.COMPLETED, o.message or "completed")
                if o.outcome == "need_confirmation":
                    if not await self._wait_confirm(tid, {"question": o.message}, deadline):
                        raise _Stop(TaskState.CANCELLED if self._cancel[tid].is_set() else TaskState.FAILED,
                                    "confirmation declined or timed out")
                    notes.append("user approved: " + o.message[:200])
                    continue
                if o.outcome == "need_more_information":
                    questions += 1
                    if questions > self.cfg.max_questions:
                        raise _Stop(TaskState.FAILED, f"asked too many questions: {o.message}")
                    reply = await self._wait_choice(tid, {
                        "kind": "question", "tool": "question", "question": o.message or "What should I do?",
                        "options": [{"id": x[:60], "label": x[:60]} for x in o.options if x.strip()], "allow_text": True,
                    }, deadline)
                    if reply is None:
                        raise _Stop(TaskState.CANCELLED if self._cancel[tid].is_set() else TaskState.FAILED,
                                    f"no answer received to: {o.message}")
                    said = reply.get("text") or reply.get("choice") or ""
                    self.store.record_action(uuid.uuid4().hex[:12], tid, history[-1]["seq"] + 1 if history else 1,
                                             "question", {"question": o.message[:300]}, "succeeded", "answered")
                    notes.append(f"The user answered your question ({o.message[:120]!r}): {said!r}. Carry on with that.")
                    continue
                raise _Stop(TaskState.FAILED, f"{o.outcome}: {o.message}")

            action = proposal.action
            assert action is not None
            if action.tool == "grant_access":           # only the server grants access, after the user said yes
                invalid += 1
                notes.append("rejected: you cannot grant access yourself; ask the user instead")
                self.store.record_action(uuid.uuid4().hex[:12], tid, seq + 1, "invalid", {}, "denied",
                                         "grant_access is not available to the model", m_ms)
                seq += 1
                if invalid >= self.cfg.max_invalid_proposals:
                    raise _Stop(TaskState.FAILED, "tried to grant itself access")
                continue
            if len(history) >= task["max_actions"]:
                raise _Stop(TaskState.FAILED, "action limit reached")

            seq += 1
            action_id = uuid.uuid4().hex[:12]
            label = action_label(action)
            params = action.model_dump(exclude={"tool"})

            # ---- deterministic guards (independent of the model)
            if history and history[-1].get("status") == "uncertain" and not self._is_observation(action):
                invalid += 1
                msg = ("the previous step's outcome is UNCERTAIN; observe first (capture_screen or a "
                       "read-only tool) before doing anything that changes state")
                notes.append(f"rejected {label}: {msg}")
                self.store.record_action(action_id, tid, seq, label, params, "denied", msg, m_ms)
                if invalid >= self.cfg.max_invalid_proposals:
                    raise _Stop(TaskState.FAILED, f"acted on an uncertain outcome: {msg}")
                continue

            if isinstance(action, AppToolCall) and action.name in ("browser.open_url", "browser.open_private"):
                url_key = str(action.args.get("url", "")).strip().rstrip("/").lower()
                if url_key in opened_urls:
                    invalid += 1
                    msg = ("you already opened this page in this task; bring its window forward with windows.focus "
                           "(a word from its title), wait for it to load, or use ui.snapshot. Do not open it again")
                    notes.append(f"rejected {label}: {msg}")
                    self.store.record_action(action_id, tid, seq, label, params, "denied", msg, m_ms)
                    history.append({"seq": seq, "tool": label, "status": "denied", "note": msg})
                    if invalid >= self.cfg.max_invalid_proposals:
                        raise _Stop(TaskState.FAILED, f"kept re-opening the same page: {msg}")
                    continue
                opened_urls.add(url_key)
                if len(opened_urls) > self.cfg.max_page_opens:
                    raise _Stop(TaskState.FAILED, f"opened more than {self.cfg.max_page_opens} different pages: stopping")

            if self.cfg.prefer_ui and action.tool in ("click", "double_click"):
                why = self._pixel_click_blocked(ui_status, shot)
                if why:
                    invalid += 1
                    notes.append(f"rejected {label}: {why}")
                    self.store.record_action(action_id, tid, seq, label, params, "denied", why, m_ms)
                    history.append({"seq": seq, "tool": label, "status": "denied", "note": why})   # counts toward the action limit
                    if invalid >= self.cfg.max_invalid_proposals:
                        raise _Stop(TaskState.FAILED, f"pixel click refused: {why}")
                    continue

            if action.tool == "capture_screen":      # a request to be shown the screen next step
                # NOT a reset of `invalid`: alternating refused clicks and captures must still run out of road
                self.store.record_action(action_id, tid, seq, label, params, "succeeded", "allow", m_ms)
                history.append({"seq": seq, "tool": label, "status": "ok", "verified": True,
                                "note": "a screenshot is attached to your next observation"})
                want_shot = True
                continue

            if action.tool in COORD_TOOLS:
                mapped = self._map_coords(action, shot)
            elif isinstance(action, AppToolCall):
                mapped = self._map_tool_coords(action, shot)
            else:
                mapped = action
            if isinstance(mapped, str):
                invalid += 1
                notes.append(f"rejected: {mapped}")
                self.store.record_action(action_id, tid, seq, label, params, "denied", mapped, m_ms)
                if invalid >= self.cfg.max_invalid_proposals:
                    raise _Stop(TaskState.FAILED, f"too many invalid proposals: {mapped}")
                continue
            action = mapped

            # ---- policy
            decision = self.policy.evaluate(action)
            if decision.verdict == "deny" and not decision.access:      # 'not approved yet' is settled on the laptop
                invalid += 1
                notes.append(f"policy denied {label}: {decision.reason}")
                self.store.record_action(action_id, tid, seq, label, params, "denied", decision.reason, m_ms)
                history.append({"seq": seq, "tool": label, "status": "denied", "note": decision.reason})
                if invalid >= self.cfg.max_invalid_proposals:
                    raise _Stop(TaskState.FAILED, f"repeated policy violations: {decision.reason}")
                continue
            sig = label + json.dumps(params, sort_keys=True, default=str)
            if (why := self._stalled(attempts.get(sig, []), self._is_observation(action))):
                invalid += 1
                notes.append(f"rejected {label}: {why}")
                self.store.record_action(action_id, tid, seq, label, params, "denied", why, m_ms)
                history.append({"seq": seq, "tool": label, "status": "denied", "note": why})
                if invalid >= self.cfg.max_invalid_proposals:
                    raise _Stop(TaskState.FAILED, f"stalled: {why}")
                continue
            invalid = 0
            self.store.record_action(action_id, tid, seq, label, params,
                                     "pending_confirmation" if decision.verdict == "confirm" else "proposed",
                                     decision.verdict, m_ms)
            if decision.verdict == "confirm":
                info = {"action_id": action_id, "tool": label, "params": self.store._scrub(params),
                        "reason": decision.reason}
                if not await self._wait_confirm(tid, info, deadline):
                    self.store.update_action(action_id, status="cancelled", error="not approved")
                    if self._cancel[tid].is_set():
                        raise _Stop(TaskState.CANCELLED, "cancelled by user")
                    history.append({"seq": seq, "tool": label, "status": "denied",
                                    "note": "user declined or approval timed out"})
                    notes.append("user declined " + label)
                    continue

            # ---- act
            self.store.transition(tid, TaskState.WAITING_FOR_ACTION, f"dispatching {label}")
            if isinstance(action, AppToolCall) and action.name.startswith("recipe."):
                entry = await self._run_recipe(tid, dev, action, deadline, seq)
                self._close_action_row(action_id, entry)
                history.append(entry)
                self._harvest(observed, entry)
                await asyncio.sleep(self.cfg.settle_delay_s)
                continue
            result, uncertain = await self._dispatch(tid, dev, action_id, action, deadline)
            acc = (result.data or {}).get("needs_access") if result is not None else None
            if acc and result.status in ("denied", "error"):
                granted = await self._handle_access(tid, dev, acc, deadline, access_state, notes)
                if isinstance(action, AppToolCall) and action.name == "ui.snapshot" and not granted:
                    self._note_snapshot(ui_status, action, result)       # remember the window is off-limits for pixel clicks
                self.store.update_action(action_id, status="denied", error=f"{result.error} (asked the user)")
                history.append({"seq": seq, "tool": label, "status": "denied",
                                "note": "needed your approval: " + ("granted, try again" if granted else "not granted")})
                continue
            if (isinstance(action, AppToolCall) and action.name == "screen.zoom"
                    and result is not None and result.ok and result.screenshot is not None):
                pending_shot = result.screenshot

            # ---- verify
            self.store.transition(tid, TaskState.VERIFYING, f"verifying {label}")
            entry = await self._verify(tid, dev, action_id, action, result, uncertain, seq)
            history.append(entry)
            self._harvest(observed, entry)
            good = entry.get("status") == "ok" and entry.get("verified") is not False
            if good:                                       # what a VERIFIED action was about (path, url, control name)
                observed.append(label + " " + json.dumps(params, default=str))
            attempts.setdefault(sig, []).append(good)
            if isinstance(action, AppToolCall) and action.name == "ui.snapshot":
                self._note_snapshot(ui_status, action, result)
            if self.cfg.record_recipes and entry.get("status") == "ok" and entry.get("verified") is not False:
                step = recordable_step(action)
                if step is not None:
                    recorded.append(step)
                elif self._needs_screen(action):          # pixel-based or focus-dependent: cannot be replayed
                    skipped += 1
            if auto and (self._needs_screen(action) or entry.get("status") in ("uncertain", "error")
                         and action.tool in GUI_TOOLS):
                want_shot = True                  # show the effect of visual actions
            await asyncio.sleep(self.cfg.settle_delay_s)

    # --------------------------------------------------------------- progress, grounding, memory
    def _stalled(self, past: list[bool], readonly: bool) -> str | None:
        """Refuse to repeat EXACTLY what already failed twice, or already succeeded without changing anything."""
        fails, oks = past.count(False), past.count(True)
        if fails >= 2:
            return ("you already tried exactly this twice and it failed: change the approach (another control, "
                    "wording or page) or stop")
        if oks >= (5 if readonly else 3):
            return (f"you already did exactly this {oks} times with the same result: it is not making progress; "
                    "try something different or finish")
        return None

    def _label_is_observation(self, label: str) -> bool:
        if label in OBSERVATION_TOOLS:
            return True
        t = get_registry().get(label)
        return bool(t and t.readonly)

    def _idle_streak(self, history: list[dict]) -> int:
        n = 0
        for h in reversed(history):
            if h.get("status") == "denied" or (h.get("status") == "ok" and self._label_is_observation(str(h.get("tool")))):
                n += 1
            else:
                break
        return n

    @staticmethod
    def _harvest(observed: list[str], entry: dict) -> None:
        """Everything the model could have read from this step becomes ground truth for later 'done' claims."""
        for v in (entry.get("untrusted_output") or {}).values():
            observed.append(str(v))
        for k in ("note", "evidence"):
            if entry.get(k):
                observed.append(str(entry[k]))
        if entry.get("data"):
            observed.append(json.dumps(entry["data"], default=str))
        while sum(map(len, observed)) > 300_000:
            observed.pop(0)

    def _experience_hints(self, goal: str, workspace: str | None = None) -> str:
        if not self.cfg.learn:
            return ""
        try:
            return hints_for(goal, self.store.list_experiences(workspace=workspace))   # never another workspace's history
        except Exception:
            return ""

    def _learn(self, tid: str, task: dict) -> None:
        if not self.cfg.learn:
            return
        try:
            exp = summarize_run(task["goal"], self.store.list_actions(tid))
            if exp is None:
                return
            ws = task.get("workspace") or "main"
            for e in self.store.list_experiences(workspace=ws):          # keep one entry per identical path
                if e.get("goal") == exp["goal"] and e.get("steps") == exp["steps"]:
                    return
            self.store.add_experience(exp, workspace=ws)
        except Exception:
            log.exception("could not store experience for %s", tid)

    # --------------------------------------------------------------- recipes
    def _save_draft(self, tid: str, task: dict, recorded: list[dict], skipped: int) -> None:
        if not (self.cfg.record_recipes and recorded):
            return
        try:
            self.store.save_recipe(build_draft(tid, task["goal"], recorded, skipped))
        except Exception:                                  # recording must never break a finished task
            log.exception("could not save draft recipe for %s", tid)

    def _close_action_row(self, action_id: str, entry: dict) -> None:
        st = {"ok": "succeeded", "uncertain": "uncertain"}.get(entry.get("status"), "failed")
        self.store.update_action(action_id, status=st, finished_at=time.time(),
                                 verification=("passed: " if entry.get("verified") else "failed: ") + str(entry.get("note", "")))

    async def _recipe_task(self, tid: str, task: dict, deadline: float, name: str, args: dict) -> None:
        dev = task["device_id"]
        await self._ensure_device(tid, dev, deadline)
        self.store.transition(tid, TaskState.RUNNING, "device available")
        action = AppToolCall(name=f"recipe.{name}", args=args)
        decision = self.policy.evaluate(action)          # unknown / unapproved recipes are 'unknown tool'
        if decision.verdict == "deny":
            raise _Stop(TaskState.FAILED, decision.reason)
        aid = uuid.uuid4().hex[:12]
        self.store.record_action(aid, tid, 1, action.name, action.model_dump(exclude={"tool"}), "proposed", "allow")
        self.store.transition(tid, TaskState.WAITING_FOR_ACTION, f"running {action.name}")
        entry = await self._run_recipe(tid, dev, action, deadline, 1)
        self._close_action_row(aid, entry)
        if entry.get("status") == "ok" and entry.get("verified"):
            self.store.transition(tid, TaskState.VERIFYING, "recipe checks passed")
            raise _Stop(TaskState.COMPLETED, str(entry.get("note", "recipe finished")))
        raise _Stop(TaskState.FAILED, str(entry.get("note", "recipe failed")))

    async def _run_recipe(self, tid: str, dev: str, call: AppToolCall, deadline: float, seq: int) -> dict:
        """Replay an approved recipe. Every step is validated, policy-checked, dispatched and verified
        exactly like a model-proposed action; the first failure stops the recipe."""
        label = call.name
        entry: dict = {"seq": seq, "tool": label}

        def fail(note: str, status: str = "error") -> dict:
            entry.update(status=status, verified=False, note=note)
            return entry

        rec = self.store.get_recipe(label.split(".", 1)[1])
        if not rec or rec.get("status") != "approved":
            return fail("recipe is not approved")
        try:
            values = bind_params(rec, call.args)
        except ValueError as e:
            return fail(str(e))

        async def run_one(step: dict, i: int | str, kind: str) -> tuple[dict, ActionResult | None]:
            act = step_action(render_step(step, values))
            sub_label = f"{label} {kind}{i} {action_label(act)}"
            d = self.policy.evaluate(act)
            params = act.model_dump(exclude={"tool"})
            sid = uuid.uuid4().hex[:12]
            if d.verdict == "deny":
                self.store.record_action(sid, tid, seq, sub_label, params, "denied", d.reason)
                return {"status": "error", "verified": False, "note": f"policy denied: {d.reason}"}, None
            self.store.record_action(sid, tid, seq, sub_label, params,
                                     "pending_confirmation" if d.verdict == "confirm" else "proposed", d.verdict)
            if d.verdict == "confirm" and not await self._wait_confirm(
                    tid, {"action_id": sid, "tool": sub_label, "params": self.store._scrub(params),
                          "reason": d.reason}, deadline):
                self.store.update_action(sid, status="cancelled", error="not approved")
                return {"status": "error", "verified": False, "note": "a step needed approval and was declined"}, None
            self.store.transition(tid, TaskState.WAITING_FOR_ACTION, f"dispatching {sub_label}")
            res, unc = await self._dispatch(tid, dev, sid, act, deadline)
            self.store.transition(tid, TaskState.VERIFYING, f"verifying {sub_label}")
            e = await self._verify(tid, dev, sid, act, res, unc, seq)
            await asyncio.sleep(self.cfg.settle_delay_s)
            return e, res

        for i, step in enumerate(rec["steps"], 1):
            try:
                e, _ = await run_one(step, i, "#")
            except ValueError as ex:
                return fail(f"step {i} is invalid: {str(ex).splitlines()[0][:120]}")
            if e.get("status") == "uncertain":
                return fail(f"step {i} has an uncertain outcome; inspect the screen before repeating", "uncertain")
            if e.get("status") != "ok" or e.get("verified") is False:
                return fail(f"step {i} failed: {e.get('note')}")

        reads: list[str] = []
        for j, v in enumerate(rec.get("verify", []), 1):
            try:
                e, res = await run_one(v["step"], j, "check")
            except ValueError as ex:
                return fail(f"check {j} is invalid: {str(ex).splitlines()[0][:120]}")
            if e.get("status") != "ok" or res is None:
                return fail(f"check {j} could not run: {e.get('note')}")
            text = str(res.data.get("text", ""))
            exp_c = render_step({"tool": "x", "v": v.get("contains")}, values)["v"] if v.get("contains") is not None else None
            exp_e = render_step({"tool": "x", "v": v.get("equals")}, values)["v"] if v.get("equals") is not None else None
            if (exp_c is not None and str(exp_c) not in text) or (exp_e is not None and text.strip() != str(exp_e).strip()):
                return fail(f"check {j} failed: expected {'contains ' + repr(exp_c) if exp_c is not None else 'equals ' + repr(exp_e)}, "
                            f"found {text[:80]!r}")
            reads.append(text[:200])
        entry.update(status="ok", verified=True,
                     note=f"recipe finished: {len(rec['steps'])} steps and {len(rec.get('verify', []))} checks passed")
        if reads:
            entry["untrusted_output"] = {"text": " | ".join(reads)}
        return entry

    # --------------------------------------------------------------- helpers
    async def _ensure_device(self, tid: str, dev: str, deadline: float) -> DeviceConnection:
        if (c := self.hub.get(dev)) is not None:
            return c
        self.store.transition(tid, TaskState.WAITING_FOR_DEVICE, "device offline")
        end = min(deadline, time.monotonic() + self.cfg.device_wait_s)
        while time.monotonic() < end:
            self._check(tid, deadline)
            if (c := await self.hub.wait_for(dev, 0.25)) is not None:
                if not self.store.device_known(dev):
                    break
                self.store.transition(tid, TaskState.RUNNING, "device reconnected")
                return c
        raise _Stop(TaskState.FAILED, "device unavailable")

    def _parse(self, raw: str | dict) -> Proposal | str:
        """Repair common format slips, then validate strictly. Errors are phrased for the model."""
        try:
            return Proposal.model_validate(normalize_proposal(extract_json(raw)))
        except ValidationError as e:
            errs = "; ".join(f"{'.'.join(str(p) for p in er['loc'])}: {er['msg']}" for er in e.errors()[:3])
            return f"malformed proposal ({errs})"[:300]
        except (ValueError, TypeError) as e:
            if not str(raw).strip():
                return "malformed proposal (the model returned an EMPTY answer; reply with one short JSON object)"
            return f"malformed proposal ({str(e).splitlines()[0]})"[:300]

    @staticmethod
    def _note_snapshot(ui_status: dict[str, str], action: AppToolCall, result: ActionResult | None) -> None:
        """Remember what ui.snapshot learned about a window: does it expose controls we can drive by name?"""
        key = str(action.args.get("window", "")).lower()
        if result is None:
            return
        if not result.ok:
            err = (result.error or "").lower()
            if key and ("not an approved" in err or "protected" in err):
                ui_status[key] = "blocked"
            return
        title = str((result.data.get("window") or {}).get("title", key)).lower() or key
        ui_status[title] = "good" if int(result.data.get("count", 0)) > 3 else "poor"

    @staticmethod
    def _pixel_click_blocked(ui_status: dict[str, str], shot: Screenshot | None) -> str | None:
        win = (shot.active_window.title if shot and shot.active_window else "").lower()
        if not win:
            return None                                   # cannot tell which app this click targets
        for k, v in ui_status.items():
            if k and (k in win or win in k):
                if v == "poor":
                    return None                           # the app exposes (almost) nothing: pixels are all we have
                if v == "blocked":
                    return f"'{shot.active_window.title}' is not approved for automation"  # type: ignore[union-attr]
                return (f"'{shot.active_window.title}' exposes controls: use ui.click / ui.type by name "  # type: ignore[union-attr]
                        "(see ui.snapshot), not pixel clicks")
        return (f"first call ui.snapshot for '{shot.active_window.title}' (window=...) to see whether its "  # type: ignore[union-attr]
                "controls can be driven by name; pixel clicks are a last resort")

    @staticmethod
    def _is_observation(a: Action) -> bool:
        if isinstance(a, AppToolCall):
            t = get_registry().get(a.name)
            return bool(t and t.readonly)
        return a.tool in OBSERVATION_TOOLS

    @staticmethod
    def _needs_screen(a: Action) -> bool:
        if isinstance(a, AppToolCall):
            t = get_registry().get(a.name)
            return bool(t and t.needs_screen)
        return a.tool in GUI_TOOLS

    @staticmethod
    def _non_idempotent(label: str) -> bool:
        if label in NON_IDEMPOTENT:
            return True
        t = get_registry().get(label)
        return bool(t and not t.idempotent)

    def _map_coords(self, a: Action, shot: Screenshot | None) -> Action | str:
        """Model coordinates are in screenshot space; the agent works in display space."""
        if shot is None:
            return "no screenshot is attached, so coordinates would be a guess: call capture_screen first"
        if not (0 <= a.x < shot.width and 0 <= a.y < shot.height):  # type: ignore[attr-defined]
            return f"coordinates ({a.x},{a.y}) outside the {shot.width}x{shot.height} screenshot"  # type: ignore[attr-defined]
        return a.model_copy(update={"x": shot.origin_x + round(a.x * shot.scale),   # type: ignore[attr-defined]
                                    "y": shot.origin_y + round(a.y * shot.scale)})  # type: ignore[attr-defined]

    def _map_tool_coords(self, a: AppToolCall, shot: Screenshot | None) -> AppToolCall | str:
        """Registry tools may take screenshot-pixel args (screen.zoom): convert like click() does."""
        t = get_registry().get(a.name)
        if t is None or not t.coord_fields:
            return a
        if shot is None:
            return "no screenshot is attached, so coordinates would be a guess: call capture_screen first"
        args = dict(a.args)
        try:
            x, y = int(args[t.coord_fields[0]]), int(args[t.coord_fields[1]])
        except (KeyError, TypeError, ValueError):
            return a                                           # the tool's own schema check will reject it
        if not (0 <= x < shot.width and 0 <= y < shot.height):
            return f"coordinates ({x},{y}) outside the {shot.width}x{shot.height} screenshot"
        args[t.coord_fields[0]] = shot.origin_x + round(x * shot.scale)
        args[t.coord_fields[1]] = shot.origin_y + round(y * shot.scale)
        for f in t.size_fields:
            if f in args:
                try:
                    args[f] = max(1, round(int(args[f]) * shot.scale))
                except (TypeError, ValueError):
                    pass
        return a.model_copy(update={"args": args})

    async def _observe(self, tid: str, conn: DeviceConnection, deadline: float) -> ActionResult | None:
        for attempt in range(self.cfg.max_dispatch_retries + 1):
            self._check(tid, deadline)
            req = ActionRequest(action_id=uuid.uuid4().hex[:12], task_id=tid,
                                action=CaptureScreen(max_dimension=self.cfg.screenshot_dim))
            t0 = time.monotonic()
            try:
                res = await conn.execute(req, self.cfg.action_timeout_s)
            except (DeviceUnavailable, ActionTimeout):
                conn = await self._ensure_device(tid, self.store.get_task(tid)["device_id"], deadline)
                continue
            self.store.add_metric(tid, "screenshot_latency", (time.monotonic() - t0) * 1000)
            if res.ok or res.status == "denied":
                return res                           # a denied capture is final: do not retry it
            if res.status == "cancelled":
                raise _Stop(TaskState.CANCELLED, "cancelled on device")
        return None

    @staticmethod
    def _access_words(acc: dict) -> tuple[str, str]:
        t = str(acc.get("target", ""))
        if acc.get("kind") == "site":
            return t, f"Deskhand wants to open {t}. Allow it?"
        if acc.get("kind") == "app":
            return t, f"Deskhand wants to open the app \u201c{t}\u201d. Allow it?"
        who = acc.get("process") or t
        return who, f"Deskhand wants to use the program {who}. Allow it?"

    async def _handle_access(self, tid: str, dev: str, acc: dict, deadline: float, state: dict, notes: list[str]) -> bool:
        """A step was blocked only because the user has not approved this yet: ask, apply the answer on the laptop."""
        kind, target = str(acc.get("kind", "")), str(acc.get("target", ""))
        key = (kind, target.lower())
        what, question = self._access_words(acc)
        if state.get(key) == "no":
            notes.append(f"The user already declined {what}; do not ask again. Use another way, or ask the user what they prefer.")
            return False
        if state.get(key) == "granted":
            notes.append(f"{what} was already approved but the step is still blocked; try a different step.")
            return False
        reply = await self._wait_choice(tid, {
            "kind": "access", "tool": "access", "question": question, "access": acc,
            "options": [{"id": "task", "label": "Allow this time"}, {"id": "always", "label": "Always allow on this computer"},
                        {"id": "no", "label": "Not now"}],
        }, deadline)
        choice = (reply or {}).get("choice")
        if self._cancel[tid].is_set():
            raise _Stop(TaskState.CANCELLED, "cancelled by user")
        if choice not in ("task", "always"):
            state[key] = "no"
            notes.append(f"The user declined access to {what}. Do not try it again: pick another way "
                         "(another app or site), or ask the user where they would like it done.")
            return False
        conn = self.hub.get(dev)
        try:
            if conn is None:
                raise DeviceUnavailable("offline")
            res = await conn.execute(ActionRequest(action_id=uuid.uuid4().hex[:12], task_id=tid, action=GrantAccess(
                kind=kind, target=target, remember=choice == "always")), self.cfg.action_timeout_s + 20)
        except (DeviceUnavailable, ActionTimeout):
            notes.append(f"Could not reach the computer to apply access to {what}.")
            return False
        if not res.ok:
            state[key] = "no"
            notes.append(f"Access to {what} could not be applied: {res.error}. Pick another way or ask the user.")
            return False
        state[key] = "granted"
        notes.append(f"The user allowed {what}" + (" (saved for next time)" if choice == "always" else " for this task") +
                     ". Repeat the step that was blocked.")
        return True

    async def _wait_choice(self, tid: str, info: dict, deadline: float) -> dict | None:
        """Wait for the user's answer to a question / access request. Returns {"choice", "text"} or None."""
        fut = asyncio.get_running_loop().create_future()
        self._confirm[tid] = fut
        self._pending_action[tid] = info
        self.store.transition(tid, TaskState.WAITING_FOR_CONFIRMATION, info.get("tool", "question"))
        try:
            timeout = min(self.cfg.question_timeout_s, max(0.1, deadline - time.monotonic()))
            res = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            res = None
        finally:
            self._confirm.pop(tid, None)
            self._pending_action.pop(tid, None)
        if isinstance(res, bool):                           # approve(True/False) used on an access card
            res = {"choice": "task", "text": ""} if res else None
        if res is not None:
            self.store.transition(tid, TaskState.RUNNING, "answered")
        return res

    async def _wait_confirm(self, tid: str, info: dict, deadline: float) -> bool:
        fut = asyncio.get_running_loop().create_future()
        self._confirm[tid] = fut
        self._pending_action[tid] = info
        self.store.transition(tid, TaskState.WAITING_FOR_CONFIRMATION, info.get("tool", "confirmation"))
        try:
            timeout = min(self.cfg.confirm_timeout_s, max(0.1, deadline - time.monotonic()))
            ok = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            ok = False
        finally:
            self._confirm.pop(tid, None)
            self._pending_action.pop(tid, None)
        if ok:
            self.store.transition(tid, TaskState.RUNNING, "approved")
        return ok

    async def _race_cancel(self, tid: str, coro) -> tuple[bool, object]:
        """Run `coro`; if cancel fires first, give it a grace period. Returns (cancelled, value)."""
        work = asyncio.ensure_future(coro)
        stop = asyncio.ensure_future(self._cancel[tid].wait())
        done, _ = await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            stop.cancel()
            return False, work.result()
        try:
            return True, await asyncio.wait_for(asyncio.shield(work), self.cfg.cancel_grace_s)
        except (asyncio.TimeoutError, DeviceUnavailable, ActionTimeout):
            work.cancel()
            return True, None

    async def _dispatch(self, tid: str, dev: str, action_id: str, action: Action,
                        deadline: float) -> tuple[ActionResult | None, bool]:
        """Returns (result, uncertain). Same action_id is reused on resend (agent dedups)."""
        timeout = self.cfg.action_timeout_s
        if action.tool == "run_command":
            timeout = action.timeout_s + 10  # type: ignore[attr-defined]
        if isinstance(action, AppToolCall):          # tools that legitimately wait (ui.wait, ui.read_page)
            for k in ("timeout_s", "max_wait_s"):
                try:
                    if k in action.args:
                        timeout = max(timeout, float(action.args[k]) + 15)
                except (TypeError, ValueError):
                    pass
        req = ActionRequest(action_id=action_id, task_id=tid, action=action)
        self.store.update_action(action_id, status="dispatched", dispatched_at=time.time())
        tries = 0
        while True:
            conn = self.hub.get(dev)
            t0 = time.monotonic()
            try:
                if conn is None:
                    raise DeviceUnavailable("offline")
                cancelled, res = await self._race_cancel(tid, conn.execute(req, timeout))
                if cancelled:
                    self.store.update_action(action_id, status="uncertain",
                                             error="cancelled while in flight; device may still be executing"
                                             if res is None else None)
                    if res is not None:
                        self._finish(action_id, res, t0)
                    raise _Stop(TaskState.CANCELLED, "cancelled by user" +
                                ("" if res is not None else " (in-flight action could not be interrupted)"))
                self._finish(action_id, res, t0)  # type: ignore[arg-type]
                return res, False  # type: ignore[return-value]
            except (DeviceUnavailable, ActionTimeout) as e:
                # Outcome unknown. Reconcile before any resend.
                self.store.update_action(action_id, status="uncertain", error=str(e))
                self.store.transition(tid, TaskState.WAITING_FOR_DEVICE, f"link lost during {action.tool}")
                conn = await self._ensure_device(tid, dev, deadline)
                self.store.transition(tid, TaskState.WAITING_FOR_ACTION, "reconciling")
                try:
                    seen = await conn.query(action_id)
                except (DeviceUnavailable, ActionTimeout):
                    return None, True
                if seen is not None:
                    self._finish(action_id, seen, t0)
                    return seen, False
                # Agent confirms it never executed the action: a resend cannot duplicate it.
                tries += 1
                self.store.update_action(action_id, retries=tries)
                if tries > self.cfg.max_dispatch_retries:
                    return None, True

    def _finish(self, action_id: str, res: ActionResult, t0: float) -> None:
        dur = (res.finished_at - res.started_at) * 1000
        self.store.add_metric(None, "action_duration", dur)
        data = {k: v for k, v in res.data.items() if k not in ("stdout", "stderr", "content", "text")} \
            if not self.store.debug_logging else res.data
        self.store.update_action(
            action_id, status="succeeded" if res.ok else "failed", finished_at=time.time(),
            duration_ms=dur, error=res.error, result=data)

    async def _verify(self, tid: str, dev: str, action_id: str, action: Action,
                      result: ActionResult | None, uncertain: bool, seq: int) -> dict:
        entry: dict = {"seq": seq, "tool": action_label(action)}
        v0 = time.monotonic()
        if uncertain or result is None:
            entry.update(status="uncertain", verified=False,
                         note="outcome unknown; do NOT assume it happened or repeat blindly - inspect the screen")
            self.store.update_action(action_id, verification="uncertain")
            return entry
        entry["status"] = result.status
        if not result.ok:
            entry.update(verified=False, note=result.error or result.status)
            self.store.update_action(action_id, verification=f"failed: {result.status}")
            return entry

        verified, evidence = True, "observed on next screenshot"
        if action.tool == "launch_application":
            verified, evidence = await self._await_ready(tid, dev, action.app_id)  # type: ignore[attr-defined]
            entry["note"] = evidence
        elif action.tool == "run_command":
            d = result.data
            verified = "exit_code" in d and not d.get("timed_out")
            evidence = f"exit_code={d.get('exit_code')} timed_out={d.get('timed_out')}"
            entry.update(exit_code=d.get("exit_code"), timed_out=d.get("timed_out"),
                         untrusted_output={"stdout": str(d.get("stdout", ""))[:2000],
                                           "stderr": str(d.get("stderr", ""))[:1000]})
        elif isinstance(action, AppToolCall):
            tool = get_registry().get(action.name)
            if tool is None:
                verified, evidence = False, "unknown tool"
            else:
                args = tool.params.model_validate(action.args)
                verified, evidence = tool.verify(args, result.data) if tool.verify else (True, "completed")
            entry["note"] = evidence
            caps = {"tree": 6000, "page_text": 8000}   # snapshots and page text need more room than a file excerpt
            out = {k: str(result.data[k])[:caps.get(k, 2000)] for k in
                   ("content", "text", "stdout", "stderr", "entries", "windows", "tree", "value_after", "page_text")
                   if k in result.data}
            if out:                               # file contents, clipboard, window titles: untrusted
                entry["untrusted_output"] = out
        elif action.tool in ("get_process_status", "get_active_window"):
            entry["data"] = result.data
        entry["verified"] = verified
        entry["evidence"] = evidence
        self.store.update_action(action_id, verification=("passed: " if verified else "failed: ") + evidence)
        self.store.add_metric(tid, "verification_latency", (time.monotonic() - v0) * 1000)
        return entry

    async def _await_ready(self, tid: str, dev: str, app_id: str) -> tuple[bool, str]:
        end = time.monotonic() + self.cfg.ready_timeout_s
        last = "no status"
        while time.monotonic() < end:
            conn = self.hub.get(dev)
            if conn is None:
                return False, "device offline during readiness check"
            try:
                res = await conn.execute(ActionRequest(
                    action_id=uuid.uuid4().hex[:12], task_id=tid,
                    action=GetProcessStatus(app_id=app_id)), self.cfg.action_timeout_s)
            except (DeviceUnavailable, ActionTimeout) as e:
                return False, f"readiness check failed: {e}"
            if res.ok and res.data.get("running") and res.data.get("window_visible"):
                title = res.data.get("title")
                return True, (f"process running and window visible (pid={res.data.get('pid')}"
                              + (f", window title '{title}'" if title else "") + ")")
            last = json.dumps(res.data) if res.ok else (res.error or res.status)
            await asyncio.sleep(self.cfg.ready_poll_s)
        return False, f"application not ready within {self.cfg.ready_timeout_s}s: {last}"

    def _completion_gate(self, history: list[dict], evidence: str, message: str = "", goal: str = "",
                         observed: list[str] | None = None) -> tuple[bool, str, str]:
        """A model claim of success is never sufficient (FR-09). Returns (ok, why, detail)."""
        ok, why = self._basic_gate(history, evidence, message, goal)
        ev = grounding_score(evidence, observed or [])
        reqs = requirements_for(goal)
        msg = grounding_score(message, observed or []) if reqs else None
        detail = f"grounding evidence={'n/a' if ev is None else f'{ev:.2f}'} message={'n/a' if msg is None else f'{msg:.2f}'}"
        if not ok:
            return False, why, detail
        if ev is not None and ev < self.cfg.ground_evidence_min:
            return False, ("your evidence is not supported by anything you actually read or saw "
                           f"(only {ev:.0%} of its details appear there): cite what the page/tool really showed"), detail
        if msg is not None and msg < self.cfg.ground_message_min:
            return False, ("your message contains details that do not appear in what you read "
                           f"(only {msg:.0%} match): report only what the page actually said"), detail
        return True, "", detail

    def _basic_gate(self, history: list[dict], evidence: str, message: str, goal: str) -> tuple[bool, str]:
        if not evidence.strip():
            return False, "no evidence cited"
        for req in requirements_for(goal):            # e.g. a chat task must READ the answer before finishing
            if not any(h.get("tool") in req["requires"] and h.get("status") == "ok" for h in history):
                return False, (f"[{req['name']}] you have not read the result yet: call "
                               f"{' or '.join(req['requires'])} first (it waits for the answer to finish)")
            if len(message.strip()) < req["min_message_chars"]:
                return False, (f"[{req['name']}] your message must CONTAIN the answer/result (at least "
                               f"{req['min_message_chars']} characters), not just say that you got one")
        for h in history:
            if h.get("status") == "uncertain" and self._non_idempotent(str(h.get("tool"))):
                return False, f"action {h['seq']} ({h['tool']}) has an uncertain outcome; inspect and resolve"
        latest: dict[str, dict] = {}   # a later successful retry supersedes an earlier failure
        for h in history:
            t = str(h.get("tool"))
            if h.get("status") != "denied" and (t in ("launch_application", "run_command") or "." in t):
                latest[t] = h
        for h in latest.values():
            if h.get("verified") is False:
                return False, f"action {h['seq']} ({h['tool']}) failed verification"
        return True, ""
