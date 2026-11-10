"""What is left of the original web folder (src/taskpilot/web/): the privacy and terms pages, their stylesheet and the
Google verification file, still served at their old /app/ addresses. The app itself is the React app (tests/test_spa.py)."""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "taskpilot" / "web"


def test_only_the_legal_pages_and_their_files_are_left():
    left = {p.name for p in WEB.iterdir() if p.is_file()}
    assert left == {"privacy.html", "terms.html", "legal.css", "theme.js", "googleaa05301828e8707d.html"}
    assert (WEB / "icons" / "icon-192.png").is_file()                 # the logo on the legal pages


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_tests_pass_under_node():
    files = sorted(str(f) for f in (Path(__file__).parent / "js").glob("*.test.mjs"))   # newer Node wants files, not a folder
    assert files, "no JavaScript test files found"
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]


def test_the_legal_pages_are_served_at_their_old_addresses_and_always_revalidated(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from taskpilot.devices import DeviceHub
    from taskpilot.model import ScriptedRuntime
    from taskpilot.orchestrator import Config, Orchestrator
    from taskpilot.policy import Policy
    from taskpilot.server import create_app
    from taskpilot.store import Store
    monkeypatch.setenv("DESKHAND_WEBAPP_DIR", str(tmp_path / "no-build"))   # the old addresses work with or without a build
    store, hub = Store(), DeviceHub()
    app = create_app(store, hub, Orchestrator(store, hub, ScriptedRuntime([]), Policy.from_dict({}), Config()), "k")
    with TestClient(app) as c:
        r = c.get("/app/privacy.html")                       # the Chrome Web Store needs a public privacy policy
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
        assert "privacy policy" in r.text.lower() and "127.0.0.1" not in r.text     # plain words only: no raw IP address on the page
        assert "no cookies" in r.text and "Last updated" in r.text and "github.com/khanfawwaz/deskhand/issues" in r.text
        assert "Delete my account and data" in r.text and "90 days" in r.text         # the policy names the self-service tools it promises
        t = c.get("/app/terms.html")
        assert t.status_code == 200 and "Terms of use" in t.text and "Last updated" in t.text and "free of charge" in t.text
        for f in ("legal.css", "theme.js", "googleaa05301828e8707d.html", "icons/icon-192.png"):
            assert c.get(f"/app/{f}").status_code == 200, f
        for gone in ("app.js", "logic.js", "style.css", "orb.js"):
            assert c.get(f"/app/{gone}").status_code == 404, gone     # the original app is gone
