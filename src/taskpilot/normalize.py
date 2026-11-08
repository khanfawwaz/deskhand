"""Repair the *shape* of model output before strict validation.

Small models know what they want to do but fumble the format: flat instead of nested, `"tool": "type"`,
`"coordinate": [x, y]`, `"keys": "ctrl+c"`. Rejecting those wastes a slow, expensive round trip, so
this layer maps the common slips onto the canonical schema. It only RESHAPES: every value still goes
through strict schema validation and the permission policy, so leniency here can never grant anything.
"""
from __future__ import annotations

import re

import json
import shlex
from typing import Any

CORE = {"capture_screen", "get_active_window", "launch_application", "click", "double_click", "type_text",
        "press_key", "hotkey", "scroll", "wait", "run_command", "get_process_status", "app_tool"}

TOOL_ALIASES = {
    "type": "type_text", "typetext": "type_text", "type_string": "type_text", "write_text": "type_text",
    "input_text": "type_text", "enter_text": "type_text", "keyboard_type": "type_text",
    "keypress": "press_key", "key": "press_key", "press": "press_key", "key_press": "press_key",
    "shortcut": "hotkey", "key_combo": "hotkey", "key_combination": "hotkey",
    "open_app": "launch_application", "launch": "launch_application", "launch_app": "launch_application",
    "open_application": "launch_application", "start_app": "launch_application", "open": "launch_application",
    "doubleclick": "double_click", "double-click": "double_click", "dblclick": "double_click",
    "left_click": "click", "mouse_click": "click",
    "screenshot": "capture_screen", "take_screenshot": "capture_screen", "look": "capture_screen",
    "capture": "capture_screen", "observe": "capture_screen",
    "run": "run_command", "shell": "run_command", "execute": "run_command", "cmd": "run_command",
    "sleep": "wait", "pause": "wait",
    "active_window": "get_active_window", "process_status": "get_process_status",
}
OUTCOME_ALIASES = {
    "complete": "done", "completed": "done", "success": "done", "finished": "done", "task_complete": "done",
    "refuse": "cannot_proceed", "refused": "cannot_proceed", "cannot": "cannot_proceed",
    "impossible": "cannot_proceed", "blocked": "cannot_proceed", "unsafe": "cannot_proceed",
    "fail": "cannot_proceed", "failed": "cannot_proceed", "stop": "cannot_proceed",
    "ask": "need_more_information", "clarify": "need_more_information", "question": "need_more_information",
    "need_info": "need_more_information", "need_clarification": "need_more_information",
    "confirm": "need_confirmation", "confirmation": "need_confirmation", "ask_confirmation": "need_confirmation",
}
OUTCOMES = {"done", "need_confirmation", "need_more_information", "cannot_proceed"}
REASONING_KEYS = ("reasoning", "thought", "thoughts", "rationale", "explanation", "why")
# Harmless commentary some models add; dropping them is safe because they carry no operational value.
DROP = {"reasoning", "thought", "thoughts", "rationale", "explanation", "why", "confidence", "comment",
        "note", "notes", "step", "id", "description"}
NESTED_ARGS = ("args", "arguments", "parameters", "params", "input")


def _as_dict(v: Any) -> dict | None:
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            j = json.loads(v)
            return j if isinstance(j, dict) else None
        except ValueError:
            return None
    return None


def _int(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return int(round(v))
    if isinstance(v, str):
        try:
            return int(round(float(v.strip())))
        except ValueError:
            return v
    return v


def _tool_name(s: str) -> str:
    s = s.strip().lower().replace(" ", "_")
    if "." in s:                       # registry tool such as files.write: leave untouched
        return s
    s = s.replace("-", "_")
    return TOOL_ALIASES.get(s, s)


def _keys_from(v: Any) -> list[str] | None:
    if isinstance(v, str):
        parts = [p.strip().lower() for p in v.replace(" ", "").split("+") if p.strip()]
        return parts or None
    if isinstance(v, (list, tuple)):
        return [str(p).strip().lower() for p in v if str(p).strip()]
    return None


def _normalize_action(f: dict) -> dict:
    f = dict(f)
    raw_tool = f.pop("tool", None)
    if raw_tool is None:
        raw_tool = f.pop("name", None) or f.pop("action", None) or f.pop("type", None)
    if not isinstance(raw_tool, str):
        raise ValueError("action has no tool name")
    tool = _tool_name(raw_tool)

    if tool == "app_tool":                       # already canonical
        args = _as_dict(f.get("args")) or {}
        return {"tool": "app_tool", "name": str(f.get("name", "")), "args": args}

    # flatten nested argument containers
    for k in NESTED_ARGS:
        if k in f and (inner := _as_dict(f[k])) is not None:
            f.pop(k)
            f = {**inner, **f}

    if "." in tool:                              # registry tool: files.write, git.status, ...
        return {"tool": "app_tool", "name": tool,
                "args": {k: v for k, v in f.items() if k not in REASONING_KEYS}}
    f = {k: v for k, v in f.items() if k not in DROP}   # commentary is only dropped for core tools

    # coordinates in other shapes
    for k in ("coordinate", "coordinates", "position", "point", "pos", "xy", "location"):
        if k in f:
            v = f.pop(k)
            if isinstance(v, (list, tuple)) and len(v) == 2:
                f["x"], f["y"] = v
            elif isinstance(v, dict) and {"x", "y"} <= set(v):
                f["x"], f["y"] = v["x"], v["y"]
            elif isinstance(v, str) and "," in v:
                a, b = v.strip("()[] ").split(",", 1)
                f["x"], f["y"] = a, b
    for k in ("x", "y", "pid", "clicks"):
        if k in f:
            f[k] = _int(f[k])

    if tool in ("click", "double_click"):
        for k in ("element", "label", "element_name", "button_name", "text"):
            if k in f and "target" not in f and isinstance(f[k], str):
                f["target"] = f.pop(k)[:120]
        if "button" in f and isinstance(f["button"], str):
            f["button"] = f["button"].lower()
        if "target" in f and isinstance(f["target"], str):
            f["target"] = f["target"][:120]
        if tool == "double_click":
            f.pop("button", None)
    elif tool == "type_text":
        for k in ("content", "value", "string", "input", "keys"):
            if k in f and "text" not in f and isinstance(f[k], str):
                f["text"] = f.pop(k)
    elif tool == "press_key":
        for k in ("keys", "key_name", "name", "value"):
            if k in f and "key" not in f:
                v = f.pop(k)
                f["key"] = v[0] if isinstance(v, list) and len(v) == 1 else v
        if isinstance(f.get("key"), str) and "+" in f["key"] and len(f["key"]) > 1:
            return {"tool": "hotkey", "keys": _keys_from(f["key"])}
        if isinstance(f.get("key"), str):
            f["key"] = f["key"].strip().lower()
    elif tool == "hotkey":
        for k in ("key", "combo", "combination", "shortcut", "keys"):
            if k in f:
                keys = _keys_from(f.pop(k))
                if keys:
                    f["keys"] = keys
                break
    elif tool == "launch_application":
        for k in ("app", "application", "name", "app_name", "program", "target"):
            if k in f and "app_id" not in f and isinstance(f[k], str):
                f["app_id"] = f.pop(k)
        if isinstance(f.get("app_id"), str):
            f["app_id"] = f["app_id"].strip().lower().removesuffix(".exe")
    elif tool == "scroll":
        direction = str(f.pop("direction", "")).lower()
        amt = next((f.pop(k) for k in ("amount", "lines", "ticks", "distance") if k in f), None)
        if "clicks" not in f and (direction or amt is not None):
            n = abs(_int(amt)) if isinstance(_int(amt), int) else 3
            f["clicks"] = -n if direction in ("down", "") else n
    elif tool == "wait":
        for k in ("duration", "time", "secs", "sec", "delay"):
            if k in f and "seconds" not in f:
                f["seconds"] = f.pop(k)
    elif tool == "run_command":
        cmd = next((f.pop(k) for k in ("command", "cmd", "program", "exe") if k in f), None)
        if isinstance(cmd, str) and "executable" not in f:
            parts = shlex.split(cmd, posix=False)
            if parts:
                f["executable"] = parts[0]
                f["args"] = [*parts[1:], *(f.get("args") or [])] if isinstance(f.get("args", []), list) else parts[1:]
        for k in ("arguments", "argv"):
            if k in f and "args" not in f:
                f["args"] = f.pop(k)
        for k in ("directory", "working_dir", "working_directory", "path", "dir"):
            if k in f and "cwd" not in f:
                f["cwd"] = f.pop(k)
        if "timeout" in f and "timeout_s" not in f:
            f["timeout_s"] = f.pop("timeout")
    elif tool == "get_process_status":
        for k in ("app", "name", "application"):
            if k in f and "app_id" not in f:
                f["app_id"] = f.pop(k)
    return {"tool": tool, **f}


def _normalize_outcome(o: Any, top: dict) -> dict:
    if isinstance(o, str):
        o = {"outcome": o}
    o = dict(o)
    kind = str(o.get("outcome") or o.get("status") or o.get("type") or "").strip().lower().replace(" ", "_")
    kind = OUTCOME_ALIASES.get(kind, kind)
    if kind not in OUTCOMES:
        raise ValueError(f"unknown outcome '{kind}'")
    raw_opts = o.get("options") or o.get("choices") or top.get("options") or []
    if isinstance(raw_opts, str):
        raw_opts = [x for x in re.split(r"[|,;/]", raw_opts)]
    options = [str(x).strip()[:60] for x in raw_opts if isinstance(x, (str, int, float)) and str(x).strip()][:6]
    return {"outcome": kind, "message": str(o.get("message") or top.get("message") or "")[:2000],
            "evidence": str(o.get("evidence") or top.get("evidence") or "")[:2000], "options": options}


def normalize_proposal(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("model output is not a JSON object")
    d = dict(raw)
    reasoning = next((str(d[k]) for k in REASONING_KEYS if k in d and isinstance(d[k], (str, int, float))), "")
    reasoning = reasoning[:2000]

    if "outcome" in d:
        return {"reasoning": reasoning, "outcome": _normalize_outcome(d["outcome"], d)}

    status = str(d.get("status", "")).strip().lower()
    if "action" not in d and "tool" not in d and "function" not in d and (
            d.get("done") is True or OUTCOME_ALIASES.get(status, status) in OUTCOMES):
        return {"reasoning": reasoning, "outcome": _normalize_outcome(
            {"outcome": "done" if d.get("done") is True else status}, d)}

    if "function" in d and isinstance(d["function"], dict):          # OpenAI-style tool_call
        fn = d["function"]
        fields = {"tool": fn.get("name"), **(_as_dict(fn.get("arguments")) or {})}
    elif isinstance(d.get("action"), dict):
        fields = dict(d["action"])
    elif isinstance(d.get("action"), str):
        fields = {"tool": d["action"], **{k: v for k, v in d.items() if k not in ("action", *REASONING_KEYS)}}
    elif "tool" in d:
        fields = {k: v for k, v in d.items() if k not in REASONING_KEYS}
    else:
        raise ValueError("no action or outcome found")
    return {"reasoning": reasoning, "action": _normalize_action(fields)}
