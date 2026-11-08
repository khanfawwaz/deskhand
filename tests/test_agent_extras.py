"""The Windows helper's supporting pieces: branding, boot stub, status file / tray logic, supervisor command."""
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from taskpilot.agent import brand, launcher
from taskpilot.agent.tray import app_url, read_status


# ------------------------------------------------------------------ the version resource Windows shows in Task Manager
def _parse_block(b: bytes, off: int = 0):
    length, vlen, wtype = struct.unpack_from("<HHH", b, off)
    end = off + 6
    while b[end:end + 2] != b"\0\0":
        end += 2
    key = b[off + 6:end].decode("utf-16-le")
    return length, vlen, wtype, key


def test_version_resource_is_well_formed_and_carries_the_name():
    data = brand.version_resource("Deskhand", "Deskhand", "Deskhand", "1.2.3.4")
    length, vlen, wtype, key = _parse_block(data)
    assert length == len(data) and key == "VS_VERSION_INFO" and vlen == 52 and wtype == 0
    for needle in ("FileDescription", "ProductName", "CompanyName", "Deskhand", "040904b0", "Translation"):
        assert needle.encode("utf-16-le") in data
    assert struct.unpack_from("<I", data, 40)[0] == 0xFEEF04BD                  # VS_FIXEDFILEINFO starts at byte 40
    assert struct.unpack_from("<II", data, 48) == ((1 << 16) | 2, (3 << 16) | 4)    # file version 1.2.3.4


def test_icon_resources_have_every_size_and_valid_images():
    group, images = brand.icon_resources()
    assert struct.unpack_from("<HHH", group) == (0, 1, len(images)) and len(group) == 6 + 14 * len(images)
    assert [n for n, _ in images] == [16, 24, 32, 48, 64, 128, 256]
    for n, data in images:
        if n < 256:
            hdr = struct.unpack_from("<IiiHH", data)
            assert hdr[0] == 40 and hdr[1] == n and hdr[2] == 2 * n and hdr[4] == 32      # DIB: double height, 32-bit
            assert len(data) == 40 + n * n * 4 + ((n + 31) // 32) * 4 * n
        else:
            assert data[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(sys.platform != "win32", reason="resource editing is a Windows API")
def test_branding_a_copy_of_pythonw_changes_its_identity(tmp_path):
    src = Path(sys.base_prefix) / "pythonw.exe"
    if not src.exists():
        pytest.skip("no pythonw.exe to copy")
    exe = tmp_path / "Deskhand.exe"
    shutil.copy(src, exe)
    brand.patch_exe(str(exe))
    raw = exe.read_bytes()
    assert "Deskhand".encode("utf-16-le") in raw and "FileDescription".encode("utf-16-le") in raw
    assert b"Python Software Foundation".decode().encode("utf-16-le") not in raw     # the generic identity is gone


# ------------------------------------------------------------------ boot stub
def test_boot_runs_a_module_from_the_given_version_folder(tmp_path):
    v = tmp_path / "v1" / "demo_pkg"
    v.mkdir(parents=True)
    (v / "__init__.py").write_text("")
    (v / "main.py").write_text("import sys\nprint('ARGS', sys.argv[1:])\n")
    boot = Path(launcher.__file__).with_name("boot.py")
    out = subprocess.run([sys.executable, str(boot), str(tmp_path / "v1"), "demo_pkg.main", "--x", "1"],
                         capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    assert out.returncode == 0, out.stderr
    assert "ARGS ['--x', '1']" in out.stdout


class Proc:
    """A fake helper process that stays alive until terminated (or exits at once with `code`)."""
    def __init__(self, code=None): self.returncode, self.terminated, self.waited = code, False, False
    def poll(self): return self.returncode
    def terminate(self): self.terminated, self.returncode = True, 1
    def kill(self): self.terminate()
    def wait(self, timeout=None): self.waited = True; return self.returncode


def _install(tmp_path, version="v1"):
    (tmp_path / "apps" / version / "taskpilot").mkdir(parents=True)
    (tmp_path / "apps" / "current.txt").write_text(version)


def test_the_supervisor_starts_the_helper_through_boot_py(tmp_path, monkeypatch):
    _install(tmp_path)
    seen = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, env=None, **kw: (seen.append((cmd, env)), Proc(0))[1])
    assert launcher.main(tmp_path) == 0
    cmd, env = seen[0]
    assert cmd[1] == str(tmp_path / "boot.py") and cmd[2] == str(tmp_path / "apps" / "v1") and cmd[3] == "taskpilot.agent.main"
    assert env["PILOT_HOME"] == str(tmp_path) and env["PILOT_VERSION"] == "v1"


def test_the_window_can_pause_and_resume_the_helper(tmp_path, monkeypatch):
    import time
    _install(tmp_path)
    procs = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, env=None, **kw: (procs.append(Proc()), procs[-1])[1])
    sup = launcher.Supervisor(tmp_path)
    sup.start()
    for _ in range(100):
        if procs:
            break
        time.sleep(0.02)
    assert sup.running and len(procs) == 1
    sup.stop()                                              # "Pause"
    assert procs[0].terminated and not sup.running
    sup.start()                                             # "Resume"
    for _ in range(100):
        if len(procs) == 2:
            break
        time.sleep(0.02)
    assert len(procs) == 2 and sup.running
    sup.stop()


def test_a_new_version_makes_the_app_restart_itself_so_its_own_code_updates(tmp_path, monkeypatch):
    _install(tmp_path)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, env=None, **kw: Proc(launcher.RESTART_FOR_UPDATE))
    called = []
    assert launcher.Supervisor(tmp_path, on_update=lambda: called.append(1))._loop() == launcher.RESTART_FOR_UPDATE
    assert called == [1]                                    # the app is told; it relaunches itself


# ------------------------------------------------------------------ starting straight from the install folder
def test_boot_root_mode_runs_the_current_version_and_sets_the_environment(tmp_path):
    pkg = tmp_path / "apps" / "v7" / "demo_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("import os, sys\nprint('V', os.environ['PILOT_VERSION'], os.environ['PILOT_HOME'] == sys.argv[0] or True, sys.argv[1:])\n")
    (tmp_path / "apps" / "current.txt").write_text("v7")
    boot = Path(launcher.__file__).with_name("boot.py")
    out = subprocess.run([sys.executable, str(boot), "--root", str(tmp_path), "demo_pkg.main", "--minimized"],
                         capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    assert out.returncode == 0, out.stderr
    assert "V v7" in out.stdout and "['--minimized']" in out.stdout


# ------------------------------------------------------------------ the app window's wording
def test_the_window_explains_each_state_plainly():
    from taskpilot.agent.app import computer_name, status_text
    assert status_text("online", False)[0] == "Online" and "control this computer" in status_text("online", False)[1]
    assert status_text("connecting", False)[0].startswith("Connecting")
    assert status_text("online", True)[0] == "Paused"                                # paused wins over the helper's state
    assert status_text("stopped", False)[0].startswith("Starting")


def test_the_window_shows_which_computer_this_is(tmp_path):
    from taskpilot.agent.app import computer_name
    assert computer_name(tmp_path) == ""
    (tmp_path / "agent.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"device_id": "OFFICE-PC-1"}).encode())
    assert computer_name(tmp_path) == "OFFICE-PC-1"
    (tmp_path / "status.json").write_text(json.dumps({"device": "OFFICE-PC-1-live"}))
    assert computer_name(tmp_path) == "OFFICE-PC-1-live"


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are a Windows feature")
def test_the_helper_is_tied_to_the_app_so_ending_the_app_ends_it():
    job = launcher._make_job()
    assert job is not None                                  # a kill-on-close job object was created


# ------------------------------------------------------------------ what the tray icon shows
def _status(home, connected, age):
    (home / "status.json").write_text(json.dumps({"connected": connected, "at": time.time() - age}))


def test_tray_state_comes_from_the_status_file(tmp_path):
    assert read_status(tmp_path)[0] == "stopped"                                  # no file
    _status(tmp_path, True, 3)
    assert read_status(tmp_path)[0] == "online"
    _status(tmp_path, False, 3)
    assert read_status(tmp_path)[0] == "connecting"
    _status(tmp_path, True, 120)
    assert read_status(tmp_path)[0] == "stopped"                                  # stale: the helper is not running
    (tmp_path / "status.json").write_text("{broken")
    assert read_status(tmp_path)[0] == "stopped"


def test_tray_opens_the_apps_address_from_the_helpers_settings(tmp_path):
    (tmp_path / "agent.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"server_url": "wss://deskhand.example.com/agent/ws"}).encode())
    assert app_url(tmp_path) == "https://deskhand.example.com/app/"
    assert app_url(tmp_path / "missing") == ""


def test_the_helper_writes_its_status_for_the_tray(tmp_path, monkeypatch):
    from taskpilot.agent import main as agent_main
    monkeypatch.setenv("PILOT_HOME", str(tmp_path))
    agent_main.write_status(True, "pc-1")
    assert read_status(tmp_path)[0] == "online"
    agent_main.write_status(False, "pc-1")
    assert read_status(tmp_path)[0] == "connecting"
    monkeypatch.delenv("PILOT_HOME")
    agent_main.write_status(True)                                                 # no home: silently does nothing
