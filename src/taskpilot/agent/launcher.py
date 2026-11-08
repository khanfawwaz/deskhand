"""Stable supervisor for the Deskhand helper (standard library only; it is the one piece that does not self-update).

Runs the helper from apps/<current version>, restarts it when it exits to apply an update (exit code 75) or crashes,
installs new Python dependencies when a version asks for them, and rolls back to the previous version if a new one
keeps dying right after start.

The Deskhand app (window + notification icon) owns a Supervisor and can start, pause and stop it. On Windows the helper
is placed in a job object, so ending the Deskhand app in Task Manager ends the helper with it: nothing is left behind.

    Deskhand.exe launcher.py <install_dir>      # headless: just supervise the helper (kept for older installs)
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

RESTART_FOR_UPDATE = 75
QUICK_FAIL_S = 30
MAX_QUICK_FAILS = 3
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode == 0:
        marker.write_text(want)
    else:
        log("pip failed: " + r.stderr[-300:])


def _make_job():
    """A Windows job object that kills everything inside it when the owning process ends (for any reason)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        return job, k
    except Exception:
        return None


class Supervisor:
    """Keeps the helper running. start()/stop() may be called from any thread (the window's buttons)."""

    def __init__(self, root: Path, on_update=None):
        self.root, self.apps = root, root / "apps"
        self.on_update = on_update              # called (from the supervisor thread) when a new version was staged
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._child: subprocess.Popen | None = None
        self._job = _make_job()
        self._lock = threading.Lock()
        self.logf = root / "launcher.log"

    def log(self, msg: str) -> None:
        try:
            if self.logf.exists() and self.logf.stat().st_size > 200_000:
                self.logf.replace(self.root / "launcher.old.log")
            with self.logf.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    @property
    def version(self) -> str:
        return read(self.apps / "current.txt")

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            if self._thread is not None:
                self._thread.join(timeout=10)
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="deskhand-supervisor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self._kill_child()
            t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=10)

    def _kill_child(self) -> None:
        c = self._child
        if c is not None and c.poll() is None:
            try:
                c.terminate()
                try:
                    c.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    c.kill()
            except OSError:
                pass

    def _interruptible_sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _loop(self) -> int:
        quick = 0
        while not self._stop.is_set():
            version = self.version
            vdir = self.apps / version
            if not version or not (vdir / "taskpilot").is_dir():
                self.log(f"no usable version '{version}'; stopping")
                return 2
            ensure_requirements(self.root, vdir, self.log)
            env = {**os.environ, "PYTHONPATH": str(vdir), "PILOT_VERSION": version, "PILOT_HOME": str(self.root)}
            # boot.py puts the version folder on sys.path itself: an embedded Python ignores PYTHONPATH
            cmd = [sys.executable, str(self.root / "boot.py"), str(vdir), "taskpilot.agent.main", "--config",
                   str(self.root / "agent.json"), "--token-file", str(self.root / "agent.token"), "--log-file",
                   str(self.root / "agent.log")]
            self.log(f"starting agent {version}")
            t0 = time.time()
            try:
                self._child = subprocess.Popen(cmd, env=env, cwd=str(self.root), creationflags=NO_WINDOW)
            except OSError as e:
                self.log(f"could not start the agent: {e}")
                self._interruptible_sleep(30)
                continue
            if self._job:                                          # ending the app ends the helper too
                try:
                    job, k = self._job
                    k.AssignProcessToJobObject(job, int(self._child._handle))      # type: ignore[attr-defined]
                except Exception:
                    pass
            while self._child.poll() is None and not self._stop.is_set():
                self._stop.wait(0.5)
            if self._stop.is_set():
                self._kill_child()
                self.log("stopped on request")
                return 0
            code, ran = self._child.returncode, time.time() - t0
            self.log(f"agent exited with {code} after {ran:.0f}s")
            if code == 0:
                return 0                                           # deliberate stop (e.g. credentials revoked)
            if code == RESTART_FOR_UPDATE:
                quick = 0
                if self.on_update:
                    self.on_update()                                # the app restarts itself so its own code updates too
                    return RESTART_FOR_UPDATE
                continue
            quick = quick + 1 if ran < QUICK_FAIL_S else 0
            if quick >= MAX_QUICK_FAILS:
                prev = read(self.apps / "previous.txt")
                if prev and prev != version and (self.apps / prev / "taskpilot").is_dir():
                    self.log(f"{version} keeps failing: rolling back to {prev}")
                    with (self.apps / "bad.txt").open("a") as f:
                        f.write(version + "\n")
                    (self.apps / "current.txt").write_text(prev)
                    quick = 0
                else:
                    self.log("keeps failing and there is no earlier version; waiting 5 minutes")
                    self._interruptible_sleep(300)
                    quick = 0
            self._interruptible_sleep(5)
        return 0


def main(root: Path) -> int:
    """Headless: supervise the helper in this process until it stops for good (older installs use this)."""
    return Supervisor(root)._loop()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent))
