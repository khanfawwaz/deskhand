"""Windows agent entry point: outbound authenticated WebSocket with reconnect + heartbeat.

Config (JSON) example:
  {"server_url": "ws://100.64.0.1:8765/agent/ws", "device_id": "rtx-laptop",
   "policy": {"approved_apps": {"notepad": "C:\\\\Windows\\\\System32\\\\notepad.exe"},
              "allowed_dirs": ["C:\\\\Users\\\\me\\\\projects"],
              "command_rules": [{"executable": "git", "allowed_subcommands": ["status", "log", "diff"]}]}}
The device token is read from the TASKPILOT_TOKEN environment variable, or from a separate token file (config key
"token_file", or --token-file); it is never stored in the config file itself.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

import websockets

from ..policy import Policy
from ..protocol import ActionRequest
from .executor import CAPABILITIES, Executor

log = logging.getLogger("taskpilot.agent")
ACTIVITY = {"inflight": 0, "last": time.time()}


def write_status(connected: bool, device_id: str = "") -> None:
    """Tell the tray icon (and anyone looking) whether we are connected. Best effort: never breaks the helper."""
    home = os.environ.get("PILOT_HOME")
    if not home:
        return
    try:
        tmp = Path(home) / "status.json.tmp"
        tmp.write_text(json.dumps({"connected": connected, "at": time.time(), "device": device_id,
                                   "version": os.environ.get("PILOT_VERSION", "")}), encoding="utf-8")
        os.replace(tmp, Path(home) / "status.json")
    except OSError:
        pass
RESTART_FOR_UPDATE = 75


async def serve_once(url: str, device_id: str, token: str, ex: Executor, heartbeat_s: float) -> None:
    async with websockets.connect(url, max_size=32 * 1024 * 1024, ping_interval=None) as ws:
        await ws.send(json.dumps({"type": "hello", "device_id": device_id, "token": token,
                                  "capabilities": CAPABILITIES}))
        ack = json.loads(await ws.recv())
        if ack.get("type") != "welcome":
            raise PermissionError(f"server rejected agent: {ack}")
        log.info("connected as %s", device_id)
        write_status(True, device_id)

        async def beat():
            while True:
                await asyncio.sleep(heartbeat_s)
                await ws.send(json.dumps({"type": "heartbeat"}))
                write_status(True, device_id)

        async def run_action(msg):
            ACTIVITY["inflight"] += 1
            try:
                res = await ex.handle(ActionRequest.model_validate(msg["request"]))
            finally:
                ACTIVITY["inflight"] -= 1
                ACTIVITY["last"] = time.time()
            await ws.send(json.dumps({"type": "result", "result": res.model_dump(mode="json")}))

        hb = asyncio.create_task(beat())
        tasks: set[asyncio.Task] = set()
        try:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "action":
                    t = asyncio.create_task(run_action(msg)); tasks.add(t); t.add_done_callback(tasks.discard)
                elif kind == "query":
                    r = ex.lookup(msg["action_id"])
                    await ws.send(json.dumps({"type": "query_result", "qid": msg["qid"],
                                              "result": r.model_dump(mode="json") if r else None}))
                elif kind == "cancel":
                    ex.cancel_task(msg["task_id"])
                elif kind == "task_end":
                    ex.end_task(msg["task_id"])
                elif kind == "estop":
                    ex.estop()
                elif kind == "revoked":
                    raise PermissionError("credentials revoked")
        finally:
            write_status(False, device_id)
            hb.cancel()
            for t in tasks:
                t.cancel()


async def connect_forever(cfg: dict, token: str, ex: Executor) -> None:
    backoff = 1.0
    write_status(False, cfg.get("device_id", ""))               # started, not connected yet
    while True:
        try:
            await serve_once(cfg["server_url"], cfg["device_id"], token, ex, cfg.get("heartbeat_s", 10))
            backoff = 1.0
        except PermissionError as e:
            log.error("%s - stopping; re-enroll the device", e)
            return
        except (OSError, websockets.WebSocketException) as e:
            log.warning("disconnected (%s); retry in %.0fs", e, backoff)
        await asyncio.sleep(backoff + random.random())
        backoff = min(backoff * 2, 30)


async def update_loop(cfg: dict, token: str) -> None:
    """Poll the server for a newer agent. Returns once one is staged and the agent is idle (then we restart)."""
    from .updater import stage_update
    home, version = os.environ.get("PILOT_HOME"), os.environ.get("PILOT_VERSION")
    if not home or not version:
        await asyncio.Future()                            # running from source: never updates, never finishes
    apps = Path(home) / "apps"
    every = float(cfg.get("update_check_s", 900))
    await asyncio.sleep(60)
    while True:
        try:
            new = await asyncio.to_thread(stage_update, cfg["server_url"], cfg["device_id"], token, apps, version)
            if new:
                log.info("update %s staged; waiting for the agent to be idle", new)
                while ACTIVITY["inflight"] or time.time() - ACTIVITY["last"] < 10:
                    await asyncio.sleep(2)
                return
        except Exception as e:                            # offline, server restarting...: try again later
            log.warning("update check failed: %s", e)
        await asyncio.sleep(every)


async def run_agent(cfg: dict, token: str, ex: Executor) -> int:
    conn = asyncio.create_task(connect_forever(cfg, token, ex))
    upd = asyncio.create_task(update_loop(cfg, token))
    await asyncio.wait({conn, upd}, return_when=asyncio.FIRST_COMPLETED)
    if upd.done() and not upd.cancelled() and upd.exception() is None and not conn.done():
        conn.cancel()
        return RESTART_FOR_UPDATE
    upd.cancel()
    return 0


def save_grant(config_path: str, kind: str, key: str, value: str | None) -> None:
    """Remember an access the user chose to 'always allow' by adding it to this computer's settings file."""
    with open(config_path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    pol = cfg.setdefault("policy", {})
    if kind == "site":
        pol.setdefault("ui_allowed_hosts", [])
        if key not in pol["ui_allowed_hosts"]:
            pol["ui_allowed_hosts"].append(key)
    elif kind == "app":
        pol.setdefault("approved_apps", {})[key] = value or key
    else:
        pol.setdefault("ui_extra_processes", [])
        if key not in pol["ui_extra_processes"]:
            pol["ui_extra_processes"].append(key)
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:                    # no BOM; written whole, then swapped in
        json.dump(cfg, f, indent=2)
    os.replace(tmp, config_path)


def _read_token(path: str | None) -> str:
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8-sig") as f:
        return f.read().strip()


def _lower_priority() -> None:
    """Background work must never compete with a game or an editor: below-normal CPU priority, idle I/O."""
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
        if os.name == "nt":
            p.ionice(psutil.IOPRIO_VERYLOW)
    except Exception:                                      # best effort: never stop the agent over this
        log.debug("could not lower priority", exc_info=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="taskpilot-agent")
    ap.add_argument("--config", default="agent.json")
    ap.add_argument("--token-file", default=None, help="file holding the device token (overrides config)")
    ap.add_argument("--log-file", default=None, help="log here (rotating) instead of the console; for background runs")
    ap.add_argument("--normal-priority", action="store_true", help="do not lower the process priority")
    args = ap.parse_args()
    if args.log_file:
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(args.log_file, maxBytes=500_000, backupCount=2,
                                                                              encoding="utf-8")],
                            format="%(asctime)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO)
    with open(args.config, encoding="utf-8-sig") as f:         # utf-8-sig: Windows PowerShell writes a BOM
        cfg = json.load(f)
    token = os.environ.get("TASKPILOT_TOKEN") or _read_token(args.token_file or cfg.get("token_file"))
    if not token:
        raise SystemExit("no device token: set TASKPILOT_TOKEN or pass --token-file")
    if not args.normal_priority:
        _lower_priority()
    from .backend import WindowsBackend
    ex = Executor(WindowsBackend(), Policy.from_dict(cfg.get("policy", {})))
    ex.persist = lambda kind, key, value: save_grant(args.config, kind, key, value)
    sys.exit(asyncio.run(run_agent(cfg, token, ex)))


if __name__ == "__main__":
    main()
