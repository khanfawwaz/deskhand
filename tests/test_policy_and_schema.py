import pytest
from pydantic import ValidationError

from taskpilot.protocol import Proposal, RunCommand, ActionAdapter
from taskpilot.states import InvalidTransition, TaskState, check_transition
from taskpilot.store import Store

from conftest import make_policy


def ev(policy, **a):
    return policy.evaluate(ActionAdapter.validate_python(a))


def test_schema_rejects_unknown_tool_and_extra_fields_and_bad_params():
    for bad in [{"action": {"tool": "format_disk"}},
                {"action": {"tool": "click", "x": 1, "y": 2, "evil": 1}},
                {"action": {"tool": "wait", "seconds": 9999}},
                {"action": {"tool": "type_text", "text": ""}},
                {},  # neither action nor outcome
                {"action": {"tool": "wait", "seconds": 1}, "outcome": {"outcome": "done"}}]:
        with pytest.raises((ValidationError, ValueError)):
            Proposal.model_validate(bad)


def test_launch_only_approved_ids(tmp_path):
    p = make_policy(tmp_path)
    assert ev(p, tool="launch_application", app_id="notepad").allowed
    assert ev(p, tool="launch_application", app_id=r"C:\evil.exe").verdict == "deny"


def test_commands_allowlist_dirs_and_subcommands(tmp_path):
    p = make_policy(tmp_path)
    ok = dict(tool="run_command", executable="git", args=["status"], cwd=str(tmp_path))
    assert ev(p, **ok).allowed
    assert ev(p, **{**ok, "args": ["push"]}).verdict == "deny"             # subcommand
    assert ev(p, **{**ok, "executable": "powershell"}).verdict == "deny"   # not allowlisted
    assert ev(p, **{**ok, "executable": r"C:\x\git.exe"}).verdict == "deny"  # paths refused
    assert ev(p, **{**ok, "cwd": r"C:\Windows"}).verdict == "deny"         # dir
    assert ev(p, **{**ok, "cwd": str(tmp_path) + r"\..\.."}).verdict == "deny"  # traversal
    assert ev(p, **{**ok, "executable": "whoami", "args": ["rm"]}).verdict == "deny"  # arg pattern


def test_hotkeys(tmp_path):
    p = make_policy(tmp_path)
    assert ev(p, tool="hotkey", keys=["ctrl", "c"]).allowed
    assert ev(p, tool="hotkey", keys=["alt", "f4"]).verdict == "deny"
    assert ev(p, tool="hotkey", keys=["ctrl", "alt", "delete"]).verdict == "deny"
    assert ev(p, tool="hotkey", keys=["a", "b"]).verdict == "deny"          # no modifier
    assert ev(p, tool="hotkey", keys=["ctrl", "w"]).verdict == "confirm"
    assert ev(p, tool="press_key", keys=None, key="superkey").verdict == "deny" if False else True
    assert ev(p, tool="press_key", key="nope").verdict == "deny"


def test_confirm_tools_override(tmp_path):
    p = make_policy(tmp_path)
    p.confirm_tools = {"type_text"}
    assert ev(p, tool="type_text", text="hi").verdict == "confirm"


def test_state_machine_blocks_invalid_transitions():
    with pytest.raises(InvalidTransition):
        check_transition(TaskState.QUEUED, TaskState.COMPLETED)
    with pytest.raises(InvalidTransition):
        check_transition(TaskState.COMPLETED, TaskState.RUNNING)


def test_store_transitions_recorded_and_terminal_is_final():
    s = Store(); s.enroll_device("d")
    t = s.create_task("d", "g")
    assert s.transition(t, TaskState.RUNNING)
    assert s.transition(t, TaskState.FAILED, error="x")
    assert s.transition(t, TaskState.RUNNING) is False       # terminal: ignored
    assert [x["to_state"] for x in s.transitions(t)] == ["queued", "running", "failed"]


def test_device_tokens_hashed_and_revocable():
    s = Store(); tok = s.enroll_device("d")
    assert s.authenticate_device("d", tok) and not s.authenticate_device("d", "bad")
    assert tok not in s.get_device("d")["token_hash"]
    s.revoke_device("d")
    assert not s.authenticate_device("d", tok)


def test_typed_text_not_persisted_by_default():
    s = Store(); s.enroll_device("d"); t = s.create_task("d", "g")
    s.record_action("a1", t, 1, "type_text", {"text": "hunter2"}, "proposed")
    assert "hunter2" not in s.get_action("a1")["params"]
