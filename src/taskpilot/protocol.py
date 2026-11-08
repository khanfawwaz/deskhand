"""Versioned wire schemas shared by the server, model layer and Windows agent."""
from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = "1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- actions

class CaptureScreen(_Strict):
    tool: Literal["capture_screen"] = "capture_screen"
    max_dimension: int = Field(1280, ge=320, le=4096)


class GetActiveWindow(_Strict):
    tool: Literal["get_active_window"] = "get_active_window"


class LaunchApplication(_Strict):
    tool: Literal["launch_application"] = "launch_application"
    app_id: str = Field(min_length=1, max_length=64)


class Click(_Strict):
    tool: Literal["click"] = "click"
    x: int
    y: int
    button: Literal["left", "right", "middle"] = "left"
    target: str = Field(default="", max_length=120)   # what is being clicked, e.g. "Don't Save button"


class DoubleClick(_Strict):
    tool: Literal["double_click"] = "double_click"
    x: int
    y: int
    target: str = Field(default="", max_length=120)


class TypeText(_Strict):
    tool: Literal["type_text"] = "type_text"
    text: str = Field(min_length=1, max_length=2000)


class PressKey(_Strict):
    tool: Literal["press_key"] = "press_key"
    key: str = Field(min_length=1, max_length=32)


class Hotkey(_Strict):
    tool: Literal["hotkey"] = "hotkey"
    keys: list[str] = Field(min_length=2, max_length=4)


class Scroll(_Strict):
    tool: Literal["scroll"] = "scroll"
    x: int
    y: int
    clicks: int = Field(ge=-50, le=50)


class Wait(_Strict):
    tool: Literal["wait"] = "wait"
    seconds: float = Field(gt=0, le=30)


class RunCommand(_Strict):
    tool: Literal["run_command"] = "run_command"
    executable: str = Field(min_length=1, max_length=128)
    args: list[str] = Field(default_factory=list, max_length=32)
    cwd: str = Field(min_length=1, max_length=260)
    timeout_s: float = Field(30, gt=0, le=300)
    max_output_bytes: int = Field(65536, ge=1024, le=1_048_576)


class GetProcessStatus(_Strict):
    tool: Literal["get_process_status"] = "get_process_status"
    app_id: str | None = Field(default=None, max_length=64)
    pid: int | None = None


class GrantAccess(_Strict):
    """Control message sent by the SERVER only after the USER said yes in the app: let the helper use an app, a
    website or a window. The model can never propose this (the orchestrator rejects it)."""
    tool: Literal["grant_access"] = "grant_access"
    kind: Literal["app", "site", "window"]
    target: str = Field(min_length=1, max_length=120)
    remember: bool = False                      # True = also saved on this computer for next time


class AppToolCall(_Strict):
    """A call to a tool from the tool registry (e.g. files.write, git.status, windows.focus).

    Args are validated against the tool's own schema by the policy layer and the agent.
    """
    tool: Literal["app_tool"] = "app_tool"
    name: str = Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$", max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


Action = Annotated[
    Union[
        CaptureScreen, GetActiveWindow, LaunchApplication, Click, DoubleClick,
        TypeText, PressKey, Hotkey, Scroll, Wait, RunCommand, GetProcessStatus, GrantAccess, AppToolCall,
    ],
    Field(discriminator="tool"),
]
ActionAdapter: TypeAdapter[Action] = TypeAdapter(Action)

NON_IDEMPOTENT = {
    "launch_application", "click", "double_click", "type_text", "press_key",
    "hotkey", "scroll", "run_command",
}
COORD_TOOLS = {"click", "double_click", "scroll"}
# Desktop primitives whose effect is only visible on screen: a fresh screenshot should follow.
GUI_TOOLS = {"launch_application", "click", "double_click", "type_text", "press_key", "hotkey", "scroll"}
# Pure observation: safe to do at any time, even right after an action with an unknown outcome.
OBSERVATION_TOOLS = {"capture_screen", "get_active_window", "get_process_status", "wait"}


def action_label(a: "Action") -> str:
    """Human/store name of an action: the registry name for app tools, else the primitive's name."""
    return a.name if isinstance(a, AppToolCall) else a.tool

# ----------------------------------------------------- non-action outcomes

class Outcome(_Strict):
    outcome: Literal["done", "need_confirmation", "need_more_information", "cannot_proceed"]
    message: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=2000)
    options: list[str] = Field(default_factory=list, max_length=6)   # need_more_information: answers to offer the user


class Proposal(_Strict):
    """What the model returns: exactly one of `action` or `outcome`."""
    reasoning: str = Field(default="", max_length=2000)
    action: Action | None = None
    outcome: Outcome | None = None

    def model_post_init(self, _ctx) -> None:
        if (self.action is None) == (self.outcome is None):
            raise ValueError("proposal must contain exactly one of 'action' or 'outcome'")


# ------------------------------------------------------- agent messaging

class ActionRequest(_Strict):
    action_id: str
    task_id: str
    action: Action
    version: str = PROTOCOL_VERSION


class WindowInfo(_Strict):
    title: str = ""
    pid: int | None = None
    process: str = ""


class Screenshot(_Strict):
    image_b64: str                # PNG, discarded after processing
    width: int                    # image size as sent
    height: int
    display_width: int            # real display size
    display_height: int
    scale: float                  # display px per image px
    dpi_scale: float = 1.0
    origin_x: int = 0             # top-left of this image in display px (non-zero for zoomed crops)
    origin_y: int = 0
    monitor: int = 1
    captured_at: float
    active_window: WindowInfo | None = None


ResultStatus = Literal["ok", "error", "denied", "timeout", "cancelled"]


class ActionResult(_Strict):
    action_id: str
    status: ResultStatus
    started_at: float = Field(default_factory=time.time)
    finished_at: float = Field(default_factory=time.time)
    error: str | None = None
    data: dict = Field(default_factory=dict)
    screenshot: Screenshot | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"
