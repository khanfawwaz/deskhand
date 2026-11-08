"""Minimal CLI over the task API."""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taskpilot")
    ap.add_argument("--url", default=os.environ.get("TASKPILOT_URL", "http://127.0.0.1:8765"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit"); s.add_argument("device"); s.add_argument("goal")
    s.add_argument("--max-actions", type=int, default=25); s.add_argument("--timeout", type=float, default=300)
    sub.add_parser("devices")
    sub.add_parser("tasks")
    for name in ("status", "actions", "cancel"):
        sub.add_parser(name).add_argument("task_id")
    a = sub.add_parser("approve"); a.add_argument("task_id"); a.add_argument("--deny", action="store_true")
    e = sub.add_parser("enroll"); e.add_argument("device_id"); e.add_argument("--name", default="")
    sub.add_parser("revoke").add_argument("device_id")
    r = sub.add_parser("recipes"); r.add_argument("--status")
    for name in ("recipe-show", "recipe-disable", "recipe-delete"):
        sub.add_parser(name).add_argument("recipe")
    ra = sub.add_parser("recipe-approve"); ra.add_argument("recipe"); ra.add_argument("--as", dest="new_name")
    ra.add_argument("--description"); ra.add_argument("--param", action="append", default=[],
                                                      help="NAME=literal seen in the recorded run (repeatable)")
    ra.add_argument("--check", action="append", default=[],
                    help='verification as JSON, e.g. \'{"step": {"tool": "ui.read", "window": "Calculator", "id": "X"}, "contains": "12"}\'')
    rr = sub.add_parser("recipe-run"); rr.add_argument("recipe"); rr.add_argument("device")
    rr.add_argument("--arg", action="append", default=[], help="NAME=value (repeatable)")
    sub.add_parser("pair")
    sub.add_parser("clients")
    sub.add_parser("unpair").add_argument("client_id")
    sub.add_parser("estop")
    sub.add_parser("metrics")
    args = ap.parse_args(argv)

    key = os.environ.get("TASKPILOT_ADMIN_KEY")
    if not key:
        print("set TASKPILOT_ADMIN_KEY", file=sys.stderr)
        return 2
    c = httpx.Client(base_url=args.url, headers={"X-API-Key": key}, timeout=30)
    m = {
        "submit": lambda: c.post("/tasks", json={"device_id": args.device, "goal": args.goal,
                                                 "max_actions": args.max_actions, "timeout_s": args.timeout}),
        "devices": lambda: c.get("/devices"), "tasks": lambda: c.get("/tasks"),
        "status": lambda: c.get(f"/tasks/{args.task_id}"),
        "actions": lambda: c.get(f"/tasks/{args.task_id}/actions"),
        "cancel": lambda: c.post(f"/tasks/{args.task_id}/cancel"),
        "approve": lambda: c.post(f"/tasks/{args.task_id}/approve", json={"approved": not args.deny}),
        "enroll": lambda: c.post("/devices", json={"device_id": args.device_id, "name": args.name}),
        "revoke": lambda: c.delete(f"/devices/{args.device_id}"),
        "estop": lambda: c.post("/estop"), "metrics": lambda: c.get("/metrics"),
        "pair": lambda: c.post("/pairing"), "clients": lambda: c.get("/clients"),
        "unpair": lambda: c.delete(f"/clients/{args.client_id}"),
        "recipes": lambda: c.get("/recipes", params={"status": args.status} if args.status else {}),
        "recipe-show": lambda: c.get(f"/recipes/{args.recipe}"),
        "recipe-disable": lambda: c.post(f"/recipes/{args.recipe}/disable"),
        "recipe-delete": lambda: c.delete(f"/recipes/{args.recipe}"),
        "recipe-approve": lambda: c.post(f"/recipes/{args.recipe}/approve", json={
            "new_name": args.new_name, "description": args.description,
            "params": dict(p.split("=", 1) for p in args.param),
            "verify": [json.loads(x) for x in args.check]}),
        "recipe-run": lambda: c.post(f"/recipes/{args.recipe}/run", json={
            "device_id": args.device, "args": dict(p.split("=", 1) for p in args.arg)}),
    }
    r = m[args.cmd]()
    print(json.dumps(r.json(), indent=2) if r.headers.get("content-type", "").startswith("application/json") else r.text)
    return 0 if r.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
