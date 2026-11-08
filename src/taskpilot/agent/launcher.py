"""Stable supervisor for the Pilot agent (standard library only; it is the one piece that does not self-update).

Runs the agent from apps/<current version>, restarts it when it exits to apply an update (exit code 75) or crashes,
installs new Python dependencies when a version asks for them, and rolls back to the previous version if a new one
keeps dying right after start.

    Deskhand.exe launcher.py <install_dir>          # the helper, restarted and updated as needed
    Deskhand.exe launcher.py <install_dir> tray     # the notification-area icon
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

RESTART_FOR_UPDATE = 75
QUICK_FAIL_S = 30
MAX_QUICK_FAILS = 3


def read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def ensure_requirements(root: Path, version_dir: Path, log) -> None:
    req = version_dir / "requirements.txt"
    if not req.exists():
        return
    want = hashlib.sha256(req.read_bytes()).hexdigest()
    marker = root / "requirements.sha"
    if read(marker) == want:
        return
    log("installing dependencies")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)],
                       capture_output=True, text=True, creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode == 0:
        marker.write_text(want)
    else:
        log("pip failed: " + r.stderr[-300:])


def main(root: Path) -> int:
    apps = root / "apps"
    logf = root / "launcher.log"

    def log(msg: str) -> None:
        try:
            if logf.exists() and logf.stat().st_size > 200_000:
                logf.replace(root / "launcher.old.log")
            with logf.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    quick = 0
    while True:
        version = read(apps / "current.txt")
        vdir = apps / version
        if not version or not (vdir / "taskpilot").is_dir():
            log(f"no usable version '{version}'; stopping")
            return 2
        ensure_requirements(root, vdir, log)
        env = {**os.environ, "PYTHONPATH": str(vdir), "PILOT_VERSION": version, "PILOT_HOME": str(root)}
        # boot.py puts the version folder on sys.path itself: an embedded Python ignores PYTHONPATH
        cmd = [sys.executable, str(root / "boot.py"), str(vdir), "taskpilot.agent.main", "--config", str(root / "agent.json"),
               "--token-file", str(root / "agent.token"), "--log-file", str(root / "agent.log")]
        log(f"starting agent {version}")
        t0 = time.time()
        code = subprocess.run(cmd, env=env, cwd=str(root), creationflags=0x08000000 if os.name == "nt" else 0).returncode
        ran = time.time() - t0
        log(f"agent exited with {code} after {ran:.0f}s")
        if code == 0:
            return 0                                             # deliberate stop (e.g. credentials revoked)
        if code == RESTART_FOR_UPDATE:
            quick = 0
            continue
        quick = quick + 1 if ran < QUICK_FAIL_S else 0
        if quick >= MAX_QUICK_FAILS:
            prev = read(apps / "previous.txt")
            if prev and prev != version and (apps / prev / "taskpilot").is_dir():
                log(f"{version} keeps failing: rolling back to {prev}")
                with (apps / "bad.txt").open("a") as f:
                    f.write(version + "\n")
                (apps / "current.txt").write_text(prev)
                quick = 0
            else:
                log("keeps failing and there is no earlier version; waiting 5 minutes")
                time.sleep(300)
                quick = 0
        time.sleep(5)


def tray(root: Path) -> int:
    """Run the notification-area icon once from the current version (it is a separate, optional process)."""
    version = read(root / "apps" / "current.txt")
    vdir = root / "apps" / version
    if not (vdir / "taskpilot").is_dir():
        return 2
    env = {**os.environ, "PILOT_HOME": str(root), "PILOT_VERSION": version}
    return subprocess.run([sys.executable, str(root / "boot.py"), str(vdir), "taskpilot.agent.tray"], env=env,
                          cwd=str(root), creationflags=0x08000000 if os.name == "nt" else 0).returncode


if __name__ == "__main__":
    home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    raise SystemExit(tray(home) if "tray" in sys.argv[2:] else main(home))
