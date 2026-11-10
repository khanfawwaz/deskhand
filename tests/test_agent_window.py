"""Deskhand's own browser window: web tasks run there, never in the owner's tabs and never taking focus."""
from pathlib import Path

import pytest

from taskpilot.agent import cdp

ROOT = Path(__file__).resolve().parents[1]


class FakeBridge:
    def __init__(self, tabs=None):
        self.sent, self.tabs = [], tabs or []
        self.connected = True

    def request(self, msg, timeout=25.0):
        self.sent.append(msg)
        c = msg["cmd"]
        if c == "tabs":
            return self.tabs
        if c == "openAgent":
            self.tabs = [{"id": 41, "url": msg["url"], "active": False}]
            return {"id": 41}
        if c == "cdp":
            return {}
        raise AssertionError(f"unexpected command {c}")


@pytest.fixture(autouse=True)
def clean():
    cdp._OWNED.clear()
    yield
    cdp._OWNED.clear()


def test_opening_a_page_uses_the_agents_own_window_and_never_focuses_anything(monkeypatch):
    b = FakeBridge(tabs=[{"id": 7, "url": "https://mail.google.com/", "active": True}])      # the owner's own tab
    monkeypatch.setattr(cdp, "_ext", lambda: b)
    page = cdp.open_page("https://www.youtube.com/")
    cmds = [m["cmd"] for m in b.sent]
    assert "openAgent" in cmds and "activate" not in cmds and "newTab" not in cmds
    assert page.tab_id == 41 and cdp._OWNED["tab"] == 41                                     # the agent's tab, not tab 7


def test_reading_a_page_never_falls_back_to_the_owners_tab(monkeypatch):
    b = FakeBridge(tabs=[{"id": 7, "url": "https://mail.google.com/inbox", "active": True}])
    monkeypatch.setattr(cdp, "_ext", lambda: b)
    with pytest.raises(cdp.BridgeError, match="web.open first"):
        cdp.current_page()                                                                    # nothing opened yet: refuse, do not peek at tab 7
    cdp.open_page("https://www.youtube.com/")
    assert cdp.current_page().tab_id == 41


def test_the_extension_opens_an_unfocused_window_of_its_own_and_makes_pages_work_while_covered():
    js = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "openAgent" in js and "focused: false" in js and "chrome.windows.create" in js
    assert "focused: true" not in js and "bringToFront" not in js
    assert "Emulation.setFocusEmulationEnabled" in js
    assert "newTab" not in js and "m.cmd === \"activate\"" not in js                         # no way left to grab the owner's tabs or focus


def test_the_debug_browser_path_opens_a_background_window_and_never_brings_it_forward():
    src = (ROOT / "src" / "taskpilot" / "agent" / "cdp.py").read_text(encoding="utf-8")
    assert '"newWindow": True' in src and '"background": True' in src and "bringToFront" not in src
    assert "SW_SHOWMINNOACTIVE" in src and "--disable-backgrounding-occluded-windows" in src


def test_the_model_is_told_it_does_not_need_to_bring_windows_forward():
    from taskpilot.prompts import build_system_prompt
    text = build_system_prompt(vision=True)
    assert "do not need windows.focus" in text.lower() or "no need to bring" in text.lower()


def test_the_extension_survives_the_app_restarting_without_leaving_tabs_attached():
    js = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "already attached" in js and "chrome.debugger.detach" in js                       # re-attach is harmless; closing lets go
