from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Union


@dataclass
class CheckResult:
    name: str
    passed: bool | None            # None = inconclusive (the verifier itself could not run: network, window gone...)
    detail: str = ""
    hard: bool = True              # soft checks (efficiency, grounding) are reported but never decide pass/fail

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail[:300], "hard": self.hard}


@dataclass
class EvalContext:
    """Everything a verifier may look at. Verifiers read the REAL end state; they never ask the model."""
    spec: "TaskSpec"
    task: dict
    actions: list[dict]
    transitions: list[dict]
    status: str
    result: str
    error: str
    duration_s: float
    model_calls: int
    backend: Any
    policy: Any
    store: Any
    sandbox: Path
    goal: str

    def executed(self) -> list[dict]:
        return [a for a in self.actions if a["status"] in ("succeeded", "failed", "uncertain")
                and a["tool"] not in ("done", "invalid")]


Verifier = Callable[[EvalContext], Union[CheckResult, Awaitable[CheckResult]]]


@dataclass
class TaskSpec:
    id: str
    goal: str                                   # may contain {sandbox}
    category: str
    verifiers: list[Verifier]
    windows: list[str] = field(default_factory=list)          # window-title allowlist for this task
    hosts: list[str] = field(default_factory=list)            # browser sites allowlist
    approved_apps: dict[str, str] = field(default_factory=dict)
    policy: dict = field(default_factory=dict)                # extra Policy keys
    max_actions: int = 30
    timeout_s: float = 240
    approve: Callable[[dict], bool] | None = None             # which pending approvals the harness may grant
    setup: Callable[[Any], Any] | None = None
    teardown: Callable[[Any], Any] | None = None
    world: Callable[[], Any] | None = None                    # sim suites: builds a fresh simulated backend per run
    tags: frozenset = frozenset()                           # live, network, chat, creates_content, gui
    description: str = ""
