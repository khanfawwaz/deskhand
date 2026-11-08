import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

WEB = Path(__file__).resolve().parents[1] / "src" / "taskpilot" / "web"


def test_manifest_makes_the_app_installable():
    m = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["display"] == "standalone" and m["start_url"] and m["name"] == "Deskhand"
    sizes = {i["sizes"]: i for i in m["icons"] if i.get("purpose") == "any"}
    assert {"192x192", "512x512"} <= set(sizes)                    # what Chrome needs to offer "Install"
    assert any(i.get("purpose") == "maskable" for i in m["icons"])
    for i in m["icons"]:
        f = WEB / i["src"]
        assert f.exists(), i["src"]
        assert Image.open(f).size == tuple(int(x) for x in i["sizes"].split("x"))


def test_index_only_references_files_that_exist_and_has_no_inline_code():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="([^"#]+)"', html):
        if "://" not in ref:
            assert (WEB / ref).exists(), ref
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)          # CSP forbids inline scripts
    assert not re.search(r"\son\w+\s*=", html)                     # ...and inline event handlers
    assert 'name="viewport"' in html and 'rel="manifest"' in html and 'lang="en"' in html


def test_every_element_the_script_uses_exists_in_the_page():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", js))
    assert not (used - ids), f"app.js uses ids missing from index.html: {sorted(used - ids)}"


def test_service_worker_precaches_real_files_and_never_touches_the_api():
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    shell = re.search(r"const SHELL = \[(.*?)\];", sw, re.S).group(1)
    for f in re.findall(r"'([^']+)'", shell):
        assert f == "./" or (WEB / f).exists(), f
    assert "startsWith('/app')" in sw and "network only" in sw


def test_the_app_never_stores_anything_but_its_own_token_and_settings():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r"ls\.(?:get|set|del)\('([^']+)'", js))
    assert keys <= {"pilot.token", "pilot.device", "pilot.targets", "pilot.settings"}
    assert "eval(" not in js and "innerHTML = '" not in js.replace("innerHTML = ''", "")   # no dynamic code / html strings


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_logic_tests_pass_under_node():
    r = subprocess.run(["node", "--test", str(Path(__file__).parent / "js")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
