"""Write the server's OpenAPI description to web-app/src/api/openapi.json, for the web app's type generator.

    python scripts/dump_openapi.py            (run it with the project's Python; `npm run gen:api` in web-app/ does this and
                                               then turns the file into src/api/schema.d.ts)

It builds the real app around a throw-away in-memory store, so no database, key or network is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taskpilot.demo import DemoModel, demo_policy  # noqa: E402
from taskpilot.devices import DeviceHub  # noqa: E402
from taskpilot.orchestrator import Config, Orchestrator  # noqa: E402
from taskpilot.server import create_app  # noqa: E402
from taskpilot.store import Store  # noqa: E402


def main() -> int:
    store, hub, policy = Store(), DeviceHub(), demo_policy()
    app = create_app(store, hub, Orchestrator(store, hub, DemoModel(), policy, Config()), "schema-only-key")
    out = ROOT / "web-app" / "src" / "api" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({len(app.openapi()['paths'])} routes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
