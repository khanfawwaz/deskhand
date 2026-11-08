import pytest
from pydantic import ValidationError

from taskpilot.model import extract_json
from taskpilot.normalize import normalize_proposal
from taskpilot.protocol import Proposal


def parse(raw):
    return Proposal.model_validate(normalize_proposal(extract_json(raw)))


def act(raw):
    return parse(raw).action


@pytest.mark.parametrize("raw,tool", [
    ({"tool": "click", "x": 1, "y": 2}, "click"),                              # flat, no "action" wrapper
    ({"action": "type", "text": "hi"}, "type_text"),                           # action is a string + alias
    ({"action": {"tool": "Double-Click", "x": 5, "y": 6}}, "double_click"),
    ({"action": {"tool": "screenshot"}}, "capture_screen"),
    ('```json\n{"action": {"tool": "capture_screen"}}\n```', "capture_screen"), # fenced
    ('Sure! {"action": {"tool": "wait", "seconds": 1}} done', "wait"),         # chatter
])
def test_shapes_and_aliases(raw, tool):
    assert act(raw).tool == tool


def test_coordinates_in_other_shapes_and_floats():
    a = act({"action": {"tool": "click", "coordinate": [10.4, 20.6]}})
    assert (a.x, a.y) == (10, 21)
    a = act({"action": {"tool": "click", "position": {"x": "7", "y": "8"}, "element": "OK button"}})
    assert (a.x, a.y, a.target) == (7, 8, "OK button")
    a = act({"action": {"tool": "double_click", "xy": "(30, 40)"}})
    assert (a.x, a.y) == (30, 40)


def test_keys_scroll_launch_wait_run():
    assert act({"action": {"tool": "hotkey", "keys": "Ctrl + C"}}).keys == ["ctrl", "c"]
    h = act({"action": {"tool": "press_key", "key": "ctrl+s"}})            # combo sent as press_key
    assert h.tool == "hotkey" and h.keys == ["ctrl", "s"]
    assert act({"action": {"tool": "press", "key": "ENTER"}}).key == "enter"
    s = act({"action": {"tool": "scroll", "x": 1, "y": 2, "direction": "down", "amount": 3}})
    assert s.clicks == -3
    assert act({"action": {"tool": "scroll", "x": 1, "y": 2, "direction": "up", "amount": 2}}).clicks == 2
    assert act({"action": {"tool": "open_app", "app": "Notepad.exe"}}).app_id == "notepad"
    assert act({"action": {"tool": "wait", "duration": 2}}).seconds == 2
    r = act({"action": {"tool": "run", "command": "git status", "directory": "C:\\work"}})
    assert (r.executable, r.args, r.cwd) == ("git", ["status"], "C:\\work")


def test_registry_tools_become_app_tool_calls_flat_or_nested():
    a = act({"action": {"tool": "files.write", "path": "C:\\w\\a.txt", "text": "hi"}})
    assert (a.tool, a.name, a.args) == ("app_tool", "files.write", {"path": "C:\\w\\a.txt", "text": "hi"})
    b = act({"action": {"tool": "git.status", "args": {"repo": "C:\\w"}}})
    assert b.name == "git.status" and b.args == {"repo": "C:\\w"}
    c = act({"function": {"name": "files.read", "arguments": '{"path": "C:\\\\w\\\\a.txt"}'}})
    assert c.name == "files.read" and c.args == {"path": "C:\\w\\a.txt"}
    # app-tool args keep keys that would be dropped as "commentary" for core tools
    d = act({"action": {"tool": "x.y", "note": "kept", "id": 3}})
    assert d.args == {"note": "kept", "id": 3}


def test_outcomes_and_aliases():
    o = parse({"outcome": "complete", "evidence": "saw it"}).outcome
    assert (o.outcome, o.evidence) == ("done", "saw it")
    assert parse({"outcome": {"outcome": "refused", "message": "no"}}).outcome.outcome == "cannot_proceed"
    assert parse({"status": "done", "evidence": "e"}).outcome.outcome == "done"
    assert parse({"done": True}).outcome.outcome == "done"
    assert parse({"outcome": {"status": "ask", "message": "which file?"}}).outcome.outcome == "need_more_information"


def test_commentary_dropped_but_unknown_fields_still_rejected():
    assert act({"action": {"tool": "click", "x": 1, "y": 2, "confidence": 0.9, "thought": "x"}}).x == 1
    with pytest.raises(ValidationError):
        parse({"action": {"tool": "click", "x": 1, "y": 2, "run_as_admin": True}})
    with pytest.raises(ValidationError):
        parse({"action": {"tool": "click", "x": 1}})                         # missing y
    with pytest.raises(ValidationError):
        parse({"action": {"tool": "app_tool", "name": "NOT A TOOL NAME"}})


def test_reasoning_extracted_and_garbage_rejected():
    assert parse({"thought": "because", "action": {"tool": "wait", "seconds": 1}}).reasoning == "because"
    for bad in ["no json at all", "[1, 2]", {}, {"foo": "bar"}, {"outcome": "flying"}, {"action": {"x": 1}}]:
        with pytest.raises((ValueError, ValidationError)):
            parse(bad)


def test_normalizer_never_grants_permission(tmp_path):
    """Leniency reshapes only: policy still decides."""
    from conftest import make_policy
    p = make_policy(tmp_path)
    assert p.evaluate(act({"action": {"tool": "open_app", "app": "regedit"}})).verdict == "deny"
    assert p.evaluate(act({"action": {"tool": "run", "command": "powershell -c x", "cwd": str(tmp_path)}})).verdict == "deny"
    assert p.evaluate(act({"action": {"tool": "files.write", "path": "C:\\Windows\\x.txt", "text": "x"}})).verdict == "deny"
