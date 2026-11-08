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


def test_the_supervisor_starts_the_helper_through_boot_py(tmp_path, monkeypatch):
    apps = tmp_path / "apps"
    (apps / "v1" / "taskpilot").mkdir(parents=True)
    (apps / "current.txt").write_text("v1")
    seen = []

    class R:
        returncode = 0

    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, env=None, **kw: (seen.append((cmd, env)), R())[1])
    assert launcher.main(tmp_path) == 0
    cmd, env = seen[0]
    assert cmd[1] == str(tmp_path / "boot.py") and cmd[2] == str(apps / "v1") and cmd[3] == "taskpilot.agent.main"
    assert env["PILOT_HOME"] == str(tmp_path) and env["PILOT_VERSION"] == "v1"


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
