"""Saved app recipes: a successful run, frozen into a reusable tool.

A recipe is a list of steps (controls referenced BY NAME, never by pixel) plus verification checks. Once a
human approves it, it appears to the model as the tool `recipe.<name>` and replays on the server WITHOUT the
model: every step is re-validated, policy-checked and verified exactly like a model-proposed action.

Safety properties:
 - steps may only use a small whitelist of tools (no files.*, run_command or raw coordinates);
 - parameters are plain values substituted into string arguments, never into tool names or structure;
 - recipes start as `draft` and do nothing until a human approves them (a poisoned page/file cannot plant one
   that runs unreviewed);
 - drafts hold the literal text the run typed, so recording is opt-in (Config.record_recipes).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from .normalize import normalize_proposal
from .policy import Decision
from .protocol import Action, ActionAdapter, AppToolCall
from .tools import Tool, ToolRegistry, get_registry

SLUG = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
PARAM = re.compile(r"^[a-z][a-z0-9_]{0,29}$")       # parameter names may be short ("a", "text")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
MAX_STEPS = 40
STEP_TOOLS = {"ui.click", "ui.type", "ui.wait", "ui.read", "windows.focus",
              "launch_application", "press_key", "hotkey", "wait"}
STATUSES = {"draft", "approved", "disabled"}


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["str", "int"] = "str"
    description: str = Field("", max_length=200)
    default: str | int | None = None
    max_length: int = Field(200, ge=1, le=2000)


class Verify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: dict                          # must be a ui.read step
    contains: str | None = Field(None, max_length=300)
    equals: str | None = Field(None, max_length=300)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="allow")     # store adds status / timestamps
    name: str
    description: str = Field("", max_length=300)
    app: str = ""
    goal: str = ""
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    steps: list[dict] = Field(default_factory=list)
    verify: list[Verify] = Field(default_factory=list)
    status: str = "draft"
    source_task_id: str = ""
    skipped_steps: int = 0                       # GUI steps that could not be recorded (pixel-based)


# --------------------------------------------------------------------------- steps

def step_action(step: dict) -> Action:
    """Flat step dict -> validated Action (same path a model proposal takes)."""
    return ActionAdapter.validate_python(normalize_proposal({"action": step})["action"])


def step_tool(step: dict) -> str:
    a = step_action(step)
    return a.name if isinstance(a, AppToolCall) else a.tool


def recordable_step(action: Action) -> dict | None:
    """Flat, replayable form of an executed action, or None if recipes may not contain it."""
    if isinstance(action, AppToolCall):
        return {"tool": action.name, **action.args} if action.name in STEP_TOOLS else None
    if action.tool in STEP_TOOLS:
        return action.model_dump(mode="json", exclude_none=True)
    return None


def _walk_strings(obj: Any, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_strings(v, fn) for k, v in obj.items()}
    return obj


def _args_problem(step: dict) -> str | None:
    """Registry-tool arguments are checked against that tool's own schema (unknown keys, bad types)."""
    a = step_action(step)
    if isinstance(a, AppToolCall):
        tool = get_registry().get(a.name)
        if tool is None:
            return f"unknown tool '{a.name}'"
        try:
            tool.params.model_validate(a.args)
        except Exception as e:
            return f"invalid arguments for {a.name}: {str(e).splitlines()[0][:100]}"
    return None


def placeholders(obj: Any) -> set[str]:
    found: set[str] = set()
    _walk_strings(obj, lambda s: found.update(PLACEHOLDER.findall(s)) or s)
    return found


def render_step(step: dict, values: dict[str, Any]) -> dict:
    """Substitute {{param}} inside string VALUES only. A lone placeholder keeps the param's type."""
    def sub(s: str):
        m = PLACEHOLDER.fullmatch(s.strip())
        if m and m.group(1) in values:
            return values[m.group(1)]
        return PLACEHOLDER.sub(lambda mm: str(values.get(mm.group(1), mm.group(0))), s)
    out = copy.deepcopy(step)
    for k, v in list(out.items()):
        if k != "tool":                              # never touch the tool name
            out[k] = _walk_strings(v, sub)
    return out


def bind_params(recipe: dict, supplied: dict) -> dict[str, Any]:
    specs = {k: ParamSpec.model_validate(v) for k, v in recipe.get("params", {}).items()}
    extra = set(supplied) - set(specs)
    if extra:
        raise ValueError(f"unknown parameter(s): {sorted(extra)}")
    values: dict[str, Any] = {}
    for k, sp in specs.items():
        if k in supplied:
            v = supplied[k]
        elif sp.default is not None:
            v = sp.default
        else:
            raise ValueError(f"missing parameter '{k}'")
        if sp.type == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"parameter '{k}' must be an integer") from None
        else:
            v = str(v)
            if len(v) > sp.max_length:
                raise ValueError(f"parameter '{k}' is longer than {sp.max_length} characters")
        values[k] = v
    return values


def validate_recipe(d: dict) -> list[str]:
    """Human-readable problems; empty list = OK to approve."""
    errs: list[str] = []
    try:
        r = Recipe.model_validate(d)
    except Exception as e:
        return [f"malformed recipe: {str(e).splitlines()[0]}"]
    if not SLUG.match(r.name):
        errs.append("name must be lowercase letters/digits/underscores, 2-40 chars, starting with a letter")
    if not r.steps:
        errs.append("recipe has no steps")
    if len(r.steps) > MAX_STEPS:
        errs.append(f"recipe has more than {MAX_STEPS} steps")
    if r.skipped_steps:
        errs.append(f"{r.skipped_steps} step(s) used pixel positions and were not recorded; "
                    "repeat the task using ui.* tools so it can be replayed reliably")
    for p in r.params:
        if not PARAM.match(p):
            errs.append(f"bad parameter name '{p}'")
    dummy = {p: ("1" if sp.type == "int" else "x") for p, sp in r.params.items()}
    used: set[str] = set()
    for i, step in enumerate(r.steps, 1):
        used |= placeholders(step)
        try:
            name = step_tool(render_step(step, dummy))
        except Exception as e:
            errs.append(f"step {i} is not a valid action: {str(e).splitlines()[0][:100]}")
            continue
        if name not in STEP_TOOLS:
            errs.append(f"step {i} uses '{name}', which recipes may not contain")
        elif (bad := _args_problem(render_step(step, dummy))):
            errs.append(f"step {i}: {bad}")
    for j, v in enumerate(r.verify, 1):
        used |= placeholders(v.step) | placeholders(v.contains or "") | placeholders(v.equals or "")
        try:
            if step_tool(render_step(v.step, dummy)) != "ui.read":
                errs.append(f"verification {j} must be a ui.read step")
            elif (bad := _args_problem(render_step(v.step, dummy))):
                errs.append(f"verification {j}: {bad}")
        except Exception as e:
            errs.append(f"verification {j} is not a valid action: {str(e).splitlines()[0][:100]}")
        if v.contains is None and v.equals is None:
            errs.append(f"verification {j} needs 'contains' or 'equals'")
    for ph in used - set(r.params):
        errs.append(f"placeholder {{{{{ph}}}}} has no matching parameter")
    return errs


def describe(recipe: dict) -> list[str]:
    """One readable line per step, for the human approval screen."""
    lines = []
    for i, s in enumerate(recipe.get("steps", []), 1):
        args = ", ".join(f"{k}={v!r}" for k, v in s.items() if k != "tool")
        lines.append(f"{i}. {s.get('tool')}({args})")
    for j, v in enumerate(recipe.get("verify", []), 1):
        exp = f"contains {v.get('contains')!r}" if v.get("contains") is not None else f"equals {v.get('equals')!r}"
        lines.append(f"check {j}. read {({k: x for k, x in v['step'].items() if k != 'tool'})} {exp}")
    return lines


# --------------------------------------------------------------------------- drafts

def build_draft(task_id: str, goal: str, steps: list[dict], skipped: int) -> dict:
    app = next((s.get("app_id") for s in steps if s.get("tool") == "launch_application"), "") or \
        next((s.get("window") for s in steps if s.get("window")), "")
    return {"name": f"draft_{task_id}", "status": "draft", "description": goal[:300], "goal": goal,
            "app": app or "", "params": {}, "steps": steps, "verify": [], "source_task_id": task_id,
            "skipped_steps": skipped}


def parametrize(recipe: dict, mapping: dict[str, str]) -> dict:
    """Turn literal values seen in the recorded run into parameters: {param: literal_text}."""
    out = copy.deepcopy(recipe)
    for param, literal in mapping.items():
        if not PARAM.match(param):
            raise ValueError(f"bad parameter name '{param}'")
        if not literal:
            raise ValueError(f"parameter '{param}' needs a non-empty example value")
        hits = 0

        def sub(s: str) -> str:
            nonlocal hits
            if literal in s:
                hits += 1
                return s.replace(literal, "{{" + param + "}}")
            return s
        out["steps"] = [{k: (v if k == "tool" else _walk_strings(v, sub)) for k, v in st.items()}
                        for st in out["steps"]]
        if hits == 0:
            raise ValueError(f"the value {literal!r} does not appear in any step, so '{param}' would do nothing")
        out.setdefault("params", {})[param] = {"type": "str", "description": f"e.g. {literal}"}
    return out


# --------------------------------------------------------------------------- tool exposure

def recipe_tool(recipe: dict) -> Tool:
    fields: dict[str, Any] = {}
    for p, raw in recipe.get("params", {}).items():
        sp = ParamSpec.model_validate(raw)
        typ = int if sp.type == "int" else str
        default = ... if sp.default is None else sp.default
        fields[p] = (typ, Field(default, max_length=sp.max_length) if typ is str else Field(default))
    model = create_model(f"Recipe_{recipe['name']}", __config__=ConfigDict(extra="forbid"), **fields)
    desc = (recipe.get("description") or recipe.get("goal") or "saved recipe")[:140]
    pdesc = "; ".join(f"{p}: {(sp.get('description') or 'text')[:40]}" for p, sp in recipe.get("params", {}).items())
    summary = f"[saved recipe] {desc}" + (f" (params, pass in the same form: {pdesc})" if pdesc else "")
    return Tool(name=f"recipe.{recipe['name']}", summary=summary[:260], params=model, handler=None,
                check=lambda a, p: Decision("allow"), verify=None, idempotent=False,
                example={k: ("1" if sp.get("type") == "int" else "...") for k, sp in recipe.get("params", {}).items()})


def sync_recipe_tools(registry: ToolRegistry, store) -> list[str]:
    """Make the registry's recipe.* tools match the APPROVED recipes in the store."""
    for n in [n for n in registry.names() if n.startswith("recipe.")]:
        registry.unregister(n)
    added = []
    for rec in store.list_recipes("approved"):
        if validate_recipe(rec):
            continue                                    # a recipe that no longer validates is never exposed
        registry.register(recipe_tool(rec))
        added.append(f"recipe.{rec['name']}")
    return added
