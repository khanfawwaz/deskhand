import hashlib
import zipfile
import io
from pathlib import Path

import httpx
import pytest

from taskpilot.agent import updater
from taskpilot.distribution import Bundle
from test_client_api import ADMIN, auth, env, pair  # noqa: F401  (fixture + helpers)


def add_code(c, tok):
    r = c.post("/v1/add-device", headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def test_the_app_mints_a_one_line_install_command(env):
    c, *_ = env
    tok = pair(c)["token"]
    r = add_code(c, tok)
    assert r["command"].startswith("irm http") and r["command"].endswith("| iex") and r["code"] in r["command"]
    assert c.post("/v1/add-device").status_code == 401                              # only a paired controller can mint one


def test_install_script_has_server_and_code_filled_in_and_nothing_else_secret(env):
    c, *_ = env
    code = add_code(c, pair(c)["token"])["code"]
    body = c.get(f"/i/{code}").text
    assert code in body and "__SERVER__" not in body and "__CODE__" not in body and "testserver" in body
    assert c.get("/i/bad code!").status_code == 404


def test_joining_enrolls_the_laptop_once_and_the_token_works(env):
    c, store, *_ = env
    code = add_code(c, pair(c)["token"])["code"]
    j = c.post("/agent/join", json={"code": code, "name": "Office PC"})
    assert j.status_code == 200
    d = j.json()
    assert d["device_id"].startswith("Office-PC-") and d["ws_url"].endswith("/agent/ws") and d["ws_url"].startswith("ws")
    assert store.authenticate_device(d["device_id"], d["token"])
    assert c.post("/agent/join", json={"code": code, "name": "again"}).status_code == 403       # one-time
    assert [x for x in c.get("/v1/devices", headers=auth(pair(c)["token"])).json() if x["device_id"] == d["device_id"]]


def test_a_laptop_code_cannot_pair_a_controller_and_vice_versa(env):
    c, *_ = env
    laptop_code = add_code(c, pair(c)["token"])["code"]
    assert c.post("/v1/pair", json={"code": laptop_code, "name": "x"}).status_code == 403
    ctrl_code = c.post("/pairing", headers=ADMIN).json()["code"]
    assert c.post("/agent/join", json={"code": ctrl_code, "name": "x"}).status_code == 403


def test_update_endpoints_need_a_device_token(env):
    c, *_ = env
    assert c.get("/agent/version").status_code == 401
    assert c.get("/agent/bundle.zip", headers={"X-Device-Id": "nope", "X-Device-Token": "bad"}).status_code == 401


def _joined(c):
    code = add_code(c, pair(c)["token"])["code"]
    d = c.post("/agent/join", json={"code": code, "name": "pc"}).json()
    return d, {"X-Device-Id": d["device_id"], "X-Device-Token": d["token"]}


def test_published_version_matches_the_bundle_and_ships_only_agent_code(env):
    c, *_ = env
    d, h = _joined(c)
    meta = c.get("/agent/version", headers=h).json()
    data = c.get("/agent/bundle.zip", headers=h).content
    assert hashlib.sha256(data).hexdigest() == meta["sha256"] and len(meta["version"]) == 12
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "taskpilot/agent/launcher.py" in names and "taskpilot/agent/main.py" in names and "requirements.txt" in names
    assert not any(n.startswith("taskpilot/web") or "/evals/" in n or "tests" in n for n in names)


def test_bundle_version_changes_when_code_changes(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    b = Bundle(tmp_path)
    v1, _ = b.build()
    assert b.build()[0] == v1
    (tmp_path / "a.py").write_text("x = 2")
    assert b.build()[0] != v1


class _Client:
    """Routes the updater's httpx calls into the in-process test app."""
    def __init__(self, tc): self.tc = tc
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, url, headers=None):
        r = self.tc.get(url.replace("http://testserver", ""), headers=headers)
        return httpx.Response(r.status_code, content=r.content, request=httpx.Request("GET", url))


def test_the_updater_stages_a_new_version_verifies_it_and_skips_known_bad_ones(env, tmp_path, monkeypatch):
    c, *_ = env
    d, h = _joined(c)
    monkeypatch.setattr(updater.httpx, "Client", lambda **kw: _Client(c))
    ws = "ws://testserver/agent/ws"
    apps = tmp_path / "apps"
    apps.mkdir()
    new = updater.stage_update(ws, d["device_id"], d["token"], apps, "old")
    assert new and (apps / new / "taskpilot" / "agent" / "main.py").exists()
    assert (apps / "current.txt").read_text() == new and (apps / "previous.txt").read_text() == "old"
    assert updater.stage_update(ws, d["device_id"], d["token"], apps, new) is None              # already current
    (apps / "bad.txt").write_text(new + "\n")                                                  # launcher rolled it back
    assert updater.stage_update(ws, d["device_id"], d["token"], apps, "old") is None


def test_a_corrupted_download_is_rejected_and_nothing_is_switched(env, tmp_path, monkeypatch):
    c, *_ = env
    d, h = _joined(c)

    class Tampered(_Client):
        def get(self, url, headers=None):
            r = super().get(url, headers)
            return httpx.Response(200, content=r.content + b"x", request=r.request) if "bundle" in url else r
    monkeypatch.setattr(updater.httpx, "Client", lambda **kw: Tampered(c))
    apps = tmp_path / "apps"
    apps.mkdir()
    with pytest.raises(ValueError, match="checksum"):
        updater.stage_update("ws://testserver/agent/ws", d["device_id"], d["token"], apps, "old")
    assert not (apps / "current.txt").exists()


class FakeProc:
    """Stands in for the helper process: exits at once with `code`."""
    def __init__(self, code): self.returncode = code
    def poll(self): return self.returncode
    def terminate(self): pass
    def kill(self): pass
    def wait(self, timeout=None): return self.returncode


def test_launcher_rolls_back_a_version_that_keeps_crashing(tmp_path, monkeypatch):
    from taskpilot.agent import launcher
    apps = tmp_path / "apps"
    for v in ("good", "bad"):
        (apps / v / "taskpilot").mkdir(parents=True)
    (apps / "current.txt").write_text("bad")
    (apps / "previous.txt").write_text("good")
    runs = []

    def fake_popen(cmd, env=None, **kw):
        runs.append(env["PILOT_VERSION"])
        return FakeProc(1 if env["PILOT_VERSION"] == "bad" else 0)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher.Supervisor, "_interruptible_sleep", lambda self, s: None)
    assert launcher.main(tmp_path) == 0
    assert runs == ["bad", "bad", "bad", "good"]
    assert (apps / "current.txt").read_text() == "good" and "bad" in (apps / "bad.txt").read_text()
