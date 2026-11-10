"""The Deskhand app (React) is served at /app/ with single-page app rules; old /app/ links redirect to it."""
import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from taskpilot import spa
from taskpilot.demo import DEMO_DEVICE, DemoConnection, DemoModel, demo_policy
from taskpilot.devices import DeviceHub
from taskpilot.orchestrator import Config, Orchestrator
from taskpilot.server import create_app
from taskpilot.store import Store

ROOT = Path(__file__).resolve().parents[1]


def make_dist(base: Path) -> Path:
    """A tiny stand-in for a real build: same shape (index.html, hashed assets/, manifest, icons)."""
    dist = base / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "icons").mkdir()
    (dist / "index.html").write_text('<!doctype html><div id="root"></div><script type="module" src="/app/assets/app-abc123.js"></script>', encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('app');", encoding="utf-8")
    (dist / "assets" / "app-abc123.css").write_text("body{margin:0}", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text('{"name":"Deskhand"}', encoding="utf-8")
    (dist / "theme-init.js").write_text("/* theme */", encoding="utf-8")
    (dist / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (base / "secret.txt").write_text("TOP SECRET OUTSIDE THE BUILD", encoding="utf-8")   # must never be reachable
    return dist


def server_app(monkeypatch, dist: Path | None):
    if dist is None:
        monkeypatch.delenv("DESKHAND_WEBAPP_DIR", raising=False)
    else:
        monkeypatch.setenv("DESKHAND_WEBAPP_DIR", str(dist))
    store, hub, policy = Store(), DeviceHub(), demo_policy()
    store.enroll_device(DEMO_DEVICE, "Demo laptop")
    hub.register(DEMO_DEVICE, DemoConnection(policy))
    orch = Orchestrator(store, hub, DemoModel(), policy, Config(settle_delay_s=0, confirm_timeout_s=10, action_timeout_s=5, ready_timeout_s=1))
    return create_app(store, hub, orch, "admin-key")


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(server_app(monkeypatch, make_dist(tmp_path)), follow_redirects=False) as c:
        yield c


# ------------------------------------------------------------------ the new app
def test_the_app_and_its_deep_links_get_index_html(client):
    for path in ("/app/", "/app/chat/abc123", "/app/privacy", "/app/some/unknown/page"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), path
        assert 'id="root"' in r.text
        assert r.headers["cache-control"] == "no-cache"                    # a release shows up at once
        assert "default-src 'self'" in r.headers["content-security-policy"]  # the same security headers as the old app


def test_the_bare_address_redirects_to_the_slash(client):
    r = client.get("/app")
    assert r.status_code == 308 and r.headers["location"] == "/app/"


def test_hashed_files_are_cached_for_a_year_and_everything_else_is_rechecked(client):
    js = client.get("/app/assets/app-abc123.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"     # not overwritten by the /app* header rule
    assert "default-src 'self'" in js.headers["content-security-policy"]
    assert client.get("/app/assets/app-abc123.css").headers["content-type"].startswith("text/css")
    for path in ("/app/manifest.webmanifest", "/app/theme-init.js", "/app/icons/icon-192.png"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
    assert client.get("/app/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")


def test_a_missing_file_is_a_404_not_a_page(client):
    for path in ("/app/assets/missing.js", "/app/nope.png", "/app/assets/app-abc123.js.map"):
        assert client.get(path).status_code == 404, path


def test_nothing_outside_the_build_folder_can_be_read(client):
    for path in ("/app/../secret.txt", "/app/%2e%2e/secret.txt", "/app/..%2fsecret.txt", "/app/assets/../../secret.txt"):
        r = client.get(path)
        assert "TOP SECRET" not in r.text, path


def test_head_requests_work(client):
    assert client.head("/app/").status_code == 200
    assert client.head("/app/assets/app-abc123.js").status_code == 200


def test_without_a_build_the_app_says_how_to_build_it_and_the_server_still_runs(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    with TestClient(server_app(monkeypatch, empty), follow_redirects=False) as c:
        r = c.get("/app/chat/abc")
        assert r.status_code == 503 and "build_web.py" in r.text
        assert c.get("/app/privacy.html").status_code == 200
        assert c.get("/v1/version").status_code == 200
    assert spa.install_spa(FastAPI(), empty) is False


def test_the_mount_and_the_directory_can_be_chosen():
    assert spa.MOUNT == "/app" and spa.OLD_MOUNT == "/app-next"
    assert spa.DEFAULT_DIR.name == "webapp" and spa.DEFAULT_DIR.parent.name == "taskpilot"


# ------------------------------------------------------------------ old addresses keep working
def test_files_from_before_the_react_app_and_old_links_still_work(client):
    for path in ("/privacy", "/terms", "/app/privacy.html", "/app/terms.html", "/app/legal.css", "/app/theme.js",
                 "/app/googleaa05301828e8707d.html"):
        assert client.get(path).status_code == 200, path
    for gone in ("/app/app.js", "/app/logic.js", "/app/style.css"):
        assert client.get(gone).status_code == 404, gone                 # the original app is gone
    assert client.get("/").headers["location"] == "/app/"                  # the front door opens the app
    r = client.get("/app-next/chat/abc?x=1")                          # a link from the preview days
    assert r.status_code == 308 and r.headers["location"] == "/app/chat/abc?x=1"
    assert client.get("/app-next").headers["location"] == "/app/"
    assert client.get("/v1/version").status_code == 200


# ------------------------------------------------------------------ the real build, when there is one
BUILT = (spa.DEFAULT_DIR / "index.html").is_file()


@pytest.mark.skipif(not BUILT, reason="the React app has not been built (python scripts/build_web.py)")
def test_the_real_build_is_served(monkeypatch):
    with TestClient(server_app(monkeypatch, None), follow_redirects=False) as c:
        r = c.get("/app/")
        assert r.status_code == 200 and 'id="root"' in r.text
        for src in [p.name for p in (spa.DEFAULT_DIR / "assets").glob("*.js")]:
            asset = c.get(f"/app/assets/{src}")
            assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]


@pytest.mark.skipif(not BUILT, reason="the React app has not been built (python scripts/build_web.py)")
def test_every_file_of_the_build_is_listed_in_package_data():
    """setuptools globs do not cross folders (`webapp/*` is one level), so each depth needs its own pattern."""
    globs = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["taskpilot"]
    import fnmatch
    missing = []
    for p in spa.DEFAULT_DIR.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(spa.DEFAULT_DIR.parent).as_posix()
        if not any(g.count("/") == rel.count("/") and fnmatch.fnmatch(rel, g) for g in globs):
            missing.append(rel)
    assert not missing, f"a normal install would leave these out: {missing}"
