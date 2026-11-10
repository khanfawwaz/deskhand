"""The server's self-update (deploy/server/deskhand-pull.sh): it must update when there is a new image, do nothing when
there is not, and go BACK to the old image when the new one does not become healthy. Docker is replaced by a tiny
stand-in, so this runs anywhere bash exists."""
import os
import subprocess
from pathlib import Path

import pytest

from conftest import working_bash

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "server" / "deskhand-pull.sh"
IMAGE = "ghcr.io/test/deskhand-server:latest"

BASH = working_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no working bash on this machine")

FAKE_DOCKER = r"""#!/usr/bin/env bash
D="$FAKE_DOCKER_DIR"
echo "$*" >> "$D/calls.log"
key() { echo "$1" | tr -c 'A-Za-z0-9\n' '_'; }
case "$1" in
  pull)    [ -f "$D/pull_fail" ] && exit 1
           [ -f "$D/remote_id" ] && cp "$D/remote_id" "$D/local_id"
           exit 0 ;;
  image)   [ -f "$D/local_id" ] && cat "$D/local_id" && exit 0
           exit 1 ;;
  tag)     src="$2"; dst="$3"; val="$src"
           [ -f "$D/tag_$(key "$src")" ] && val=$(cat "$D/tag_$(key "$src")")
           echo "$val" > "$D/tag_$(key "$dst")"
           case "$dst" in *deskhand-server:latest) echo "$val" > "$D/local_id" ;; esac
           exit 0 ;;
  compose) echo "up $(cat "$D/local_id" 2>/dev/null)" >> "$D/ups.log"
           [ -f "$D/up_fail" ] && exit 1
           exit 0 ;;
  inspect) cat "$D/health" 2>/dev/null || echo missing
           exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def box(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    d = tmp_path / "state"
    d.mkdir()
    shim = bin_dir / "docker"
    shim.write_bytes(FAKE_DOCKER.replace("\r\n", "\n").encode())
    shim.chmod(0o755)
    work = tmp_path / "deploy"
    work.mkdir()

    def run(local=None, remote=None, health="healthy", pull_fail=False, up_fail=False):
        for f in d.iterdir():
            f.unlink()
        if local:
            (d / "local_id").write_text(local + "\n")
        if remote:
            (d / "remote_id").write_text(remote + "\n")
        (d / "health").write_text(health + "\n")
        if pull_fail:
            (d / "pull_fail").write_text("x")
        if up_fail:
            (d / "up_fail").write_text("x")
        env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "FAKE_DOCKER_DIR": str(d),
               "DESKHAND_DEPLOY_DIR": str(work), "DESKHAND_IMAGE": IMAGE, "DESKHAND_HEALTH_WAIT": "6"}
        r = subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        ups = (d / "ups.log").read_text().split("\n") if (d / "ups.log").exists() else []
        return r, [u for u in ups if u], (d / "local_id").read_text().strip() if (d / "local_id").exists() else None

    return run


def test_nothing_new_does_nothing(box):
    r, ups, now = box(local="sha256:A", remote="sha256:A")
    assert r.returncode == 0 and ups == [] and now == "sha256:A"


def test_a_new_healthy_image_is_started_once(box):
    r, ups, now = box(local="sha256:A", remote="sha256:B")
    assert r.returncode == 0 and ups == ["up sha256:B"] and now == "sha256:B"
    assert "updated and healthy" in r.stdout


def test_a_new_image_that_is_not_healthy_is_rolled_back(box):
    r, ups, now = box(local="sha256:A", remote="sha256:B", health="unhealthy")
    assert r.returncode == 1
    assert ups == ["up sha256:B", "up sha256:A"]          # started the new one, then went back to the old one
    assert now == "sha256:A" and "ROLLED BACK" in r.stdout


def test_a_failed_download_changes_nothing(box):
    r, ups, now = box(local="sha256:A", remote="sha256:B", pull_fail=True)
    assert r.returncode == 1 and ups == [] and now == "sha256:A" and "pull failed" in r.stdout


def test_the_first_install_starts_the_image(box):
    r, ups, now = box(local=None, remote="sha256:B")
    assert r.returncode == 0 and ups == ["up sha256:B"] and now == "sha256:B"


def test_a_new_image_that_will_not_start_is_rolled_back(box):
    r, ups, now = box(local="sha256:A", remote="sha256:B", up_fail=True)
    assert r.returncode == 1 and "could not start the new image" in r.stdout and now == "sha256:A"


def test_the_image_can_say_which_commit_it_was_built_from(monkeypatch):
    from taskpilot import distribution
    monkeypatch.setenv("DESKHAND_COMMIT", "abc1234")
    assert distribution._git_commit() == "abc1234"
    monkeypatch.setenv("DESKHAND_COMMIT", "not hex; rm -rf")      # anything that is not a short hex id is ignored
    assert distribution._git_commit() != "not hex; rm -rf"


def test_the_workflows_fit_together():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pub = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert ci.startswith("name: tests") and "workflows: [tests]" in pub          # publish waits for exactly this workflow
    assert "branches: [main]" in pub and "conclusion == 'success'" in pub          # only a green main is ever published
    assert "secrets.GITHUB_TOKEN" in pub and "SSH" not in pub.upper().replace("SSHD", "")   # no server login keys in GitHub
    assert "deploy/docker/Dockerfile" in pub and (ROOT / "deploy" / "docker" / "Dockerfile").exists()
