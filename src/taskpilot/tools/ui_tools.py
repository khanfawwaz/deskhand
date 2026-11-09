"""UI Automation tools (read/act on app controls by name) and screen.zoom (legible crops for small targets).

Which windows may be automated is decided on the AGENT, per call, from the window actually resolved
(title + process) - the server cannot know that. The server-side check only validates argument shape.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from ..policy import Decision, Policy
from .registry import Tool, ToolContext, ToolRegistry, NeedsAccess


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotArgs(_P):
    window: str = Field("", max_length=120)          # part of the window title; empty = foreground window
    max_elements: int = Field(80, ge=5, le=200)
    query: str = Field("", max_length=60)            # only controls whose name/id contains this
    name_chars: int = Field(90, ge=20, le=800)       # raise (e.g. 400) to read long text such as headlines or answers
    role: str = Field("", max_length=30)             # only this kind of control, e.g. Hyperlink (a page's links), Button, Edit
    skip: int = Field(0, ge=0, le=2000)              # page through a long list: skip the first N matches (see the hint)
    min_chars: int = Field(0, ge=0, le=200)          # only controls whose name is at least this long: content (video titles,
                                                      # headlines, results), not short menu labels


class Selector(_P):
    window: str = Field("", max_length=120)
    name: str = Field("", max_length=120)
    role: str = Field("", max_length=30)             # Button, Edit, CheckBox, ListItem, MenuItem, ...
    id: str = Field("", max_length=120)              # AutomationId shown by ui.snapshot
    nth: int | None = Field(None, ge=1, le=50)       # which match, when several controls share a name


class ClickArgs(Selector):
    double: bool = False


class TypeArgs(Selector):
    text: str = Field(max_length=2000)


class WaitArgs(Selector):
    timeout_s: float = Field(10, gt=0, le=30)
    gone: bool = False                               # wait for the control to DISAPPEAR instead


class ReadPageArgs(_P):
    window: str = Field("", max_length=120)          # empty = the foreground window
    min_wait_s: float = Field(6, ge=0, le=60)        # always wait at least this long (answers start late)
    quiet_s: float = Field(3, ge=1, le=15)           # ...then until the text has not changed for this long
    max_wait_s: float = Field(60, gt=0, le=120)
    max_chars: int = Field(4000, ge=200, le=8000)    # the END of the page text is returned (the newest content)


class ZoomArgs(_P):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=16, le=4000)
    height: int = Field(ge=16, le=4000)


def _sel(a: Selector) -> dict:
    return {"name": a.name, "role": a.role, "id": a.id, "nth": a.nth}


def _check_sel(a: Selector, p: Policy) -> Decision:
    if not (a.name or a.id):
        return Decision("deny", "give the control's name or id (see ui.snapshot); a role alone is too vague")
    if isinstance(a, TypeArgs) and len(a.text) > p.max_type_len:
        return Decision("deny", "text exceeds configured maximum length")
    if isinstance(a, ClickArgs) and a.name.strip().lower() in {n.lower() for n in p.ui_confirm_names}:
        return Decision("confirm", f"'{a.name}' may be irreversible (sends, deletes or pays)")
    return Decision("allow")


async def _window(ctx: ToolContext, title: str) -> tuple[dict | None, str | None]:
    """Resolve the target window and apply the app gate BEFORE touching anything."""
    win = await asyncio.to_thread(ctx.backend.ui_window, title)
    if win is None:
        return None, (f"no visible window with '{title}' in its title" if title else "no foreground window")
    from ..agent.gate import window_allowed
    d = await window_allowed(ctx.policy, ctx.backend, win["title"], win["process"])
    if not d.allowed:
        reason = (f"a window matching '{title}' is not approved" if ctx.policy.cloud_safe else d.reason)   # no private titles
        if d.access:
            from ..agent.gate import window_access
            raise NeedsAccess(reason, await window_access(ctx.policy, ctx.backend, win["title"], win["process"], d))
        return None, (f"a window matching '{title}' is protected" if ctx.policy.cloud_safe else d.reason)
    return win, None


async def _snapshot(ctx: ToolContext, a: SnapshotArgs) -> dict:
    win, err = await _window(ctx, a.window)
    if err:
        return {"error": err}
    return await asyncio.to_thread(ctx.backend.ui_snapshot, win, a.max_elements, a.query, a.name_chars, a.role, a.skip, a.min_chars)


async def _act(ctx: ToolContext, a: Selector, action: str, text: str | None = None) -> dict:
    win, err = await _window(ctx, a.window)
    if err:
        return {"error": err}
    return await asyncio.to_thread(ctx.backend.ui_act, win, _sel(a), action, text)


async def _click(ctx: ToolContext, a: ClickArgs) -> dict:
    return await _act(ctx, a, "double_click" if a.double else "click")


async def _type(ctx: ToolContext, a: TypeArgs) -> dict:
    return await _act(ctx, a, "type", a.text)


async def _read(ctx: ToolContext, a: Selector) -> dict:
    return await _act(ctx, a, "read")


async def _wait(ctx: ToolContext, a: WaitArgs) -> dict:
    win, err = await _window(ctx, a.window)
    if err:
        return {"error": err}
    return await asyncio.to_thread(ctx.backend.ui_wait, win, _sel(a), a.timeout_s, a.gone)


async def _read_page(ctx: ToolContext, a: ReadPageArgs) -> dict:
    win, err = await _window(ctx, a.window)
    if err:
        return {"error": err}
    return await asyncio.to_thread(ctx.backend.ui_read_page, win, a.min_wait_s, a.quiet_s, a.max_wait_s, a.max_chars)


async def _zoom(ctx: ToolContext, a: ZoomArgs) -> dict:
    shot = await asyncio.to_thread(ctx.backend.screenshot_region, a.x, a.y, a.width, a.height, 1000)
    # The executor lifts "_screenshot" into ActionResult.screenshot (an image does not belong in data).
    return {"_screenshot": shot.model_dump(), "region": [a.x, a.y, a.width, a.height]}


def _verify_type(a: TypeArgs, d: dict) -> tuple[bool, str]:
    if d.get("method") == "value" and d.get("readback_available"):
        ok = d.get("value_after") == a.text
        return ok, ("field value read back and matches" if ok
                    else f"field now holds {str(d.get('value_after'))[:60]!r}, not the requested text")
    if d.get("readback_available"):
        ok = a.text in str(d.get("value_after"))
        return ok, "typed by keyboard; read back contains the text" if ok else "typed by keyboard; read back does NOT contain the text"
    return True, "typed by keyboard; read-back not possible, confirm with ui.read or a screenshot"


def register_ui_tools(r: ToolRegistry) -> None:
    R = r.register
    R(Tool("ui.snapshot", "List an app window's controls by name (role, name, id, value). Use this FIRST in a new app.",
           SnapshotArgs, _snapshot, None, lambda a, d: (True, f"{d.get('shown')} of {d.get('count')} controls"),
           readonly=True, idempotent=True, example={"window": "Calculator"}))
    R(Tool("ui.click", "Click a control by name/id (exact, no pixels). Add nth if several share a name.",
           ClickArgs, _click, _check_sel,
           lambda a, d: (True, f"{d.get('method')} on {d.get('element')}"),
           example={"window": "Calculator", "name": "Seven", "role": "Button"}))
    R(Tool("ui.type", "Put text into an input field by name/id. Read back and checked when the app allows.",
           TypeArgs, _type, _check_sel, _verify_type,
           example={"window": "Notepad", "name": "Text editor", "text": "hello"}))
    R(Tool("ui.read", "Read a control's text/value (e.g. a display or field). The way to VERIFY a result.",
           Selector, _read, _check_sel, lambda a, d: (True, f"read {len(str(d.get('text', '')))} chars from {d.get('source')}"),
           readonly=True, idempotent=True, example={"window": "Calculator", "id": "CalculatorResults"}))
    R(Tool("ui.wait", "Wait up to timeout_s for a control to appear (or disappear with gone=true).",
           WaitArgs, _wait, _check_sel, lambda a, d: (bool(d.get("met")), f"condition met after {d.get('waited_s')}s"),
           readonly=True, idempotent=True, example={"window": "Calculator", "name": "Equals"}))
    R(Tool("ui.read_page", "WAIT until the page stops changing (e.g. a chat answer finishes streaming), then return its "
           "main text. Use it to read results; never finish a research/chat task before calling it.",
           ReadPageArgs, _read_page, None,
           lambda a, d: (bool(d.get("page_text")), f"read {d.get('chars')} chars; stable={d.get('stable')}"
                         + ("" if d.get("stable") else " (still changing: reread if needed)")),
           readonly=True, idempotent=True, example={"window": ""}))
    R(Tool("screen.zoom", "Show an ENLARGED crop of the last screenshot (x,y,width,height in its pixels). Use before "
           "clicking small targets; then click on the zoomed image.",
           ZoomArgs, _zoom, None, lambda a, d: (True, "enlarged crop attached to your next observation"),
           readonly=True, idempotent=True, coord_fields=("x", "y"), size_fields=("width", "height"),
           example={"x": 900, "y": 60, "width": 200, "height": 120}))
