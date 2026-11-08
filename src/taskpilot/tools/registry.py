"""Tool registry: one definition per tool = schema + policy check + agent handler + verifier.

Adding a tool never touches the orchestrator or state machine: register it and it is advertised to the
model (prompt generated from here), policy-checked on server and agent, executed, and verified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

from pydantic import BaseModel

from ..policy import Decision, Policy

if TYPE_CHECKING:
    from ..agent.backend import Backend
    from ..agent.runner import ManagedProcesses


@dataclass
class ToolContext:
    """What a handler may use on the agent. Handlers never see the model or the network."""
    backend: "Backend"
    policy: Policy
    managed: "ManagedProcesses"
    task_id: str


Check = Callable[[Any, Policy], Decision]                 # (args, policy) -> verdict
Handler = Callable[[ToolContext, Any], Awaitable[dict]]   # runs on the agent, returns structured data
Verify = Callable[[Any, dict], tuple[bool, str]]          # SERVER-side: (args, result data) -> (ok, evidence)


@dataclass
class Tool:
    name: str                       # "files.write"
    summary: str                    # one line shown to the model
    params: type[BaseModel]
    handler: Handler
    check: Optional[Check] = None
    verify: Optional[Verify] = None
    readonly: bool = False          # pure observation: allowed even while an earlier outcome is uncertain
    idempotent: bool = False        # safe to repeat blindly
    example: dict = field(default_factory=dict)
    needs_screen: bool = False      # visual effect: attach a screenshot next step
    # Args the model gives in SCREENSHOT pixels; the orchestrator converts them to display pixels
    # (coord_fields: positions, size_fields: lengths) exactly as it does for click().
    coord_fields: tuple = ()
    size_fields: tuple = ()

    def signature(self) -> str:
        parts = []
        for k, f in self.params.model_fields.items():
            ann = getattr(f.annotation, "__name__", str(f.annotation)).replace("typing.", "")
            default = "" if f.is_required() else f"={f.default!r}"
            parts.append(f"{k}:{ann}{default}")
        return f"{self.name}({', '.join(parts)})"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in self.names()]

    def prompt_section(self) -> str:
        """Compact tool list for the system prompt (generated, so it can never drift from the code)."""
        lines = []
        for t in self.all():
            ex = ""
            if t.example:
                import json
                ex = "  e.g. " + json.dumps({"tool": t.name, **t.example}, ensure_ascii=False)
            lines.append(f"- {t.signature()} - {t.summary}{ex}")
        return "\n".join(lines)


_default: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _default
    if _default is None:
        from .builtin import register_builtin
        _default = ToolRegistry()
        register_builtin(_default)
    return _default
