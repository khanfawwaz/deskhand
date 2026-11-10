"""Run the WHOLE of Deskhand on this laptop, from this source folder, without touching the real server or your installed copy.

    python scripts/run_local.py            # start everything, press Alt+F8, Ctrl+C to stop
    python scripts/run_local.py --reset    # forget the local test data and start clean

It starts three things, all local:
  1. a Deskhand server on 127.0.0.1 (your NVIDIA key from .env, the two-model setup, a database of its own),
  2. this computer as a device of that server,
  3. the real Deskhand app (window, tray icon, the Alt+F8 shortcut) running THIS code. It runs beside your installed Deskhand,
     with its own window ("Deskhand (dev)"), and never updates itself away from this folder.

Your installed Deskhand, the live server and your real sign-ins are not used. Nothing is pushed anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# what this computer is allowed to do in the test: the same kind of rules the real install uses
POLICY = {
    "approved_apps": {"calc": r"C:\Windows\System32\calc.exe", "notepad": r"C:\Windows\System32\notepad.exe"},
    "allowed_dirs": [str(Path.home() / "Documents" / "Deskhand")],
    "ui_allowed_windows": ["Calculator", "Notepad"],
    "restrict_input_to_approved": True,
    "cloud_safe": True,
    "require_site_approval": True,          # a new website is a question you answer (with buttons)
    "ui_allowed_hosts": ["youtube.com"],
}


def load_dotenv() -> None:
    f = ROOT / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def free_port(want: int) -> int:
    for p in range(want, want + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("no free port near %d" % want)


def call(method: str, url: str, key: str = "", body: dict | None = None, timeout: float = 10):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"X-API-Key": key} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip().startswith((b"{", b"[")) else raw.decode()


def make_layout(home: Path) -> None:
    """The same folder layout an installed Deskhand has, with the 'version' pointing at THIS source folder."""
    (home / "apps").mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC / "taskpilot" / "agent" / "boot.py", home / "boot.py")
    shutil.copy(SRC / "taskpilot" / "agent" / "launcher.py", home / "launcher.py")
    link = home / "apps" / "dev"
    if not link.exists():
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(SRC)], check=True, capture_output=True)    # a folder link: edits show at once
    (home / "apps" / "current.txt").write_text("dev")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--home", default=str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Deskhand-dev"))
    ap.add_argument("--reset", action="store_true", help="forget the local test database and device, and start clean")
    ap.add_argument("--no-app", action="store_true", help="start only the server and the device (no window, no shortcut)")
    a = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("this runs the Windows app, so it needs Windows")
    load_dotenv()
    if not os.environ.get("NVIDIA_API_KEY"):
        raise SystemExit("NVIDIA_API_KEY is not set (put it in .env in the project folder)")

    home = Path(a.home)
    if a.reset and home.exists():
        # only ever our own dev folder; remove the junction first so the source folder is never touched
        link = home / "apps" / "dev"
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    make_layout(home)

    key_file = home / "admin.key"
    admin = key_file.read_text().strip() if key_file.exists() else secrets.token_urlsafe(24)
    key_file.write_text(admin)
    (home / "server_policy.json").write_text(json.dumps(POLICY, indent=2), encoding="utf-8")
    port = free_port(a.port)
    base = f"http://127.0.0.1:{port}"

    env = {**os.environ, "PYTHONPATH": str(SRC), "TASKPILOT_ADMIN_KEY": admin, "TASKPILOT_SIGNUP": "closed",
           "TASKPILOT_TEXT_MODEL": os.environ.get("TASKPILOT_TEXT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
           "TASKPILOT_VISION_MODEL": os.environ.get("TASKPILOT_VISION_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
           "TASKPILOT_SPLIT_MODE": "describe", "TASKPILOT_ALLOW_IMAGES": "1"}
    log = (home / "server.log").open("ab")
    server = subprocess.Popen([sys.executable, "-m", "taskpilot.server", "--host", "127.0.0.1", "--port", str(port),
                               "--db", str(home / "server.db"), "--policy", str(home / "server_policy.json"), "--provider", "openai"],
                              env=env, cwd=str(home), stdout=log, stderr=subprocess.STDOUT)
    app = None
    try:
        print(f"starting the local Deskhand server on {base} ...", flush=True)
        for _ in range(60):
            try:
                call("GET", base + "/v1/auth/config")
                break
            except (urllib.error.URLError, OSError):
                if server.poll() is not None:
                    raise SystemExit(f"the server stopped at once; see {home / 'server.log'}")
                time.sleep(0.5)
        else:
            raise SystemExit(f"the server did not start; see {home / 'server.log'}")

        device = "dev-" + os.environ.get("COMPUTERNAME", "laptop").lower()
        token_file = home / "agent.token"
        known = {d["device_id"] for d in call("GET", base + "/devices", admin)}
        if device in known and not token_file.exists():
            call("DELETE", base + f"/devices/{device}", admin)                    # its token was lost: make a fresh one
            known.discard(device)
        if device not in known:
            tok = call("POST", base + "/devices", admin, {"device_id": device, "name": "This laptop (local test)", "workspace": "main"})["token"]
            token_file.write_text(tok)
        (home / "agent.json").write_text(json.dumps({
            "server_url": f"ws://127.0.0.1:{port}/agent/ws", "device_id": device, "heartbeat_s": 10, "no_update": True,
            "policy": POLICY, **({"hotkey": json.loads((home / "agent.json").read_text()).get("hotkey")}
                                 if (home / "agent.json").exists() and json.loads((home / "agent.json").read_text()).get("hotkey") else {})
        }, indent=2), encoding="utf-8")

        if a.no_app:
            print("server and device ready (no app). Ctrl+C to stop.")
        else:
            app_env = {**os.environ, "DESKHAND_DEV": "dev", "PYTHONPATH": str(SRC)}
            app = subprocess.Popen([sys.executable, str(home / "boot.py"), "--root", str(home), "taskpilot.agent.app", "--show"],
                                   env=app_env, cwd=str(home))
            for _ in range(40):                                                    # wait for this computer to come online
                time.sleep(1)
                online = [d for d in call("GET", base + "/devices", admin) if d["device_id"] == device and d["online"]]
                if online:
                    break
            print()
            print("=" * 66)
            print("  Deskhand is running locally on this laptop.")
            print("    server        :", base, "(its own database, your NVIDIA key)")
            print("    this computer :", device, "- online" if online else "- NOT online yet (see %s)" % (home / "agent.log"))
            print("    window        : 'Deskhand (dev)' (and a tray icon near the clock)")
            print()
            print("  Press Alt+F8, say a command, and watch the prompt.  Try:")
            print("      'open calculator and add seven and five'")
            print("      'open notepad and write hello'")
            print("      'open youtube and play the latest video from NetworkChuck'   (it will ask you to allow the site)")
            print("  Change the shortcut with the 'Change shortcut...' button in the window.")
            print("  Press Ctrl+C here to stop everything. Logs:", home)
            print("=" * 66, flush=True)
        while server.poll() is None and (app is None or app.poll() is None):
            time.sleep(1)
        print("\nstopped.")
    except KeyboardInterrupt:
        print("\nstopping ...")
    finally:
        for p in (app, server):
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
