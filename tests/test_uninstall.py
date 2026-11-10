"""Deskhand shows up under Windows' Installed apps and its Uninstall really removes it (tested on a throw-away folder
and a throw-away registry key: the real install, its tasks and its shortcuts are never touched)."""
import os
import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")

from taskpilot.agent import uninstall  # noqa: E402


def _key(name):
    import winreg
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall.UNINSTALL_KEY + "\\" + name)


def test_registers_an_installed_app_entry_and_uninstall_removes_everything(tmp_path):
    import winreg
    name = "Deskhand-test-" + uuid.uuid4().hex[:8]
    home = tmp_path / "Deskhand"
    (home / "apps").mkdir(parents=True)
    (home / "apps" / "current.txt").write_text("abc123def456")
    (home / "chrome-profile").mkdir()
    (home / "chrome-profile" / "Cookies").write_text("x")
    (home / "agent.token").write_text("secret")

    assert uninstall.register(home, name) is True
    try:
        with _key(name) as k:
            get = lambda v: winreg.QueryValueEx(k, v)[0]
            assert get("DisplayName") == "Deskhand" and get("DisplayVersion") == "abc123def456"
            assert get("InstallLocation") == str(home)
            assert "uninstall.ps1" in get("UninstallString") and get("QuietUninstallString").endswith("-Quiet")
            assert get("NoModify") == 1
        assert (home / "uninstall.ps1").read_bytes().startswith(b"\xef\xbb\xbf")
        assert uninstall.register(home, name) is True                    # repeating is harmless

        # run the real uninstall script in test mode (no tasks, no shortcuts, no questions)
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(home / "uninstall.ps1"),
                        "-Quiet", "-Test", "-KeyName", name], check=True, timeout=60, cwd=str(tmp_path))
        for _ in range(40):
            if not home.exists():
                break
            time.sleep(0.25)
        assert not home.exists(), "the Deskhand folder should be gone"
        with pytest.raises(FileNotFoundError):
            _key(name)
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, uninstall.UNINSTALL_KEY + "\\" + name)
        except OSError:
            pass


def test_a_development_copy_never_registers(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKHAND_DEV", "dev")
    assert uninstall.register(tmp_path) is False
    assert not (tmp_path / "uninstall.ps1").exists()
