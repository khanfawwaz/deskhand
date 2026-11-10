"""Model runtime boundary. The model proposes; it never executes."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from .prompts import build_system_prompt

@dataclass
class ModelContext:
    goal: str
    state: str
    history: list[dict]
    screenshot_b64: str | None
    screenshot_size: tuple[int, int] | None
    active_window: str = ""
    approved_apps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # orchestrator feedback (rejections etc.)
    vision: bool = True                              # may the model ever be shown a screenshot?
    playbook: str = ""                               # trusted tips for the apps named in the goal
    screen_text: str = ""                            # the screen as TEXT, written by a vision model for a text-only decider
    workspace: str = ""                              # whose task this is (for fair queueing and usage tracking)
    task_id: str = ""


class ModelRuntime(ABC):
    @abstractmethod
    async def propose(self, ctx: ModelContext) -> str | dict:
        """Return raw proposal JSON (str or dict). Validation happens in the orchestrator."""


def render_user_message(ctx: ModelContext) -> str:
    return json.dumps({
        "goal": ctx.goal, "task_state": ctx.state,
        "screenshot": ({"attached": True, "size": ctx.screenshot_size, "active_window": ctx.active_window}
                       if ctx.screenshot_b64 else
                       {"attached": False, "described_below": True, "size": ctx.screenshot_size,
                        "active_window": ctx.active_window} if ctx.screen_text else
                       {"attached": False, **({"hint": "call capture_screen to see the screen"}
                                              if ctx.vision else {})}),
        **({"screen_description": ctx.screen_text} if ctx.screen_text else {}),
        "approved_app_ids": ctx.approved_apps,
        "recent_history": ctx.history[-12:],
        "orchestrator_notes": ctx.notes[-5:],
        **({"app_tips": ctx.playbook} if ctx.playbook else {}),
    }, ensure_ascii=False)


class OllamaRuntime(ModelRuntime):
    def __init__(self, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 180):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    async def propose(self, ctx: ModelContext) -> str:
        msg: dict = {"role": "user", "content": render_user_message(ctx)}
        if ctx.screenshot_b64:
            msg["images"] = [ctx.screenshot_b64]
        body = {"model": self.model, "stream": False, "format": "json",
                "options": {"temperature": 0.1},
                "messages": [{"role": "system", "content": build_system_prompt(vision=True)}, msg]}
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.host}/api/chat", json=body)
            r.raise_for_status()
            return r.json()["message"]["content"]


def extract_json(raw: "str | dict") -> dict:
    """Tolerate ```json fences / chatter around one JSON object. The schema stays strict."""
    if isinstance(raw, dict):
        return raw
    s = raw.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    start = s.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                esc = (ch == "\\" and not esc)
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


class ScriptedRuntime(ModelRuntime):
    """Deterministic runtime for tests and benchmarks: replays queued proposals."""

    def __init__(self, script: list[str | dict]):
        self.script = list(script)
        self.contexts: list[ModelContext] = []

    async def propose(self, ctx: ModelContext) -> str | dict:
        self.contexts.append(ctx)
        if not self.script:
            return {"outcome": {"outcome": "cannot_proceed", "message": "script exhausted"}}
        return self.script.pop(0)
