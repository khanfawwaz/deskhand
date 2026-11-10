"""Finding an installed app by the name someone used: 'riotclient' must find the Start-menu entry 'Riot Client'."""
import json
from types import SimpleNamespace

import pytest

from taskpilot.agent import backend as be
from taskpilot.agent.backend import pick_start_app

APPS = [
    {"Name": "Riot Client", "AppID": "C:\\Riot Games\\Riot Client\\RiotClientServices.exe"},
    {"Name": "VALORANT", "AppID": "C:\\Riot Games\\VALORANT\\live\\VALORANT.exe"},
    {"Name": "Notepad++", "AppID": "Notepad++"},
    {"Name": "Notepad", "AppID": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
    {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {"Name": "Spotify", "AppID": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"},
]


@pytest.mark.parametrize("asked,expected", [
    ("riotclient", "Riot Client"),            # the case from the screenshot: one word vs 'Riot Client'
    ("Riot Client", "Riot Client"),
    ("riot-client", "Riot Client"),
    ("riot", "Riot Client"),
    ("valorant", "VALORANT"),
    ("notepad", "Notepad"),                   # exact beats 'Notepad++'
    ("calc", "Calculator"),
    ("spotify music", "Spotify"),             # extra words around a real name still find it
    ("riotclientservices", "Riot Client"),    # a longer technical name still finds the short Start-menu name
])
def test_names_match_whatever_spacing_the_model_used(asked, expected):
    got = pick_start_app(asked, APPS)
    assert (got["Name"] if got else None) == expected


def test_nothing_or_junk_finds_nothing():
    assert pick_start_app("", APPS) is None and pick_start_app("   ", APPS) is None
    assert pick_start_app("!!", APPS) is None
    assert pick_start_app("zzzz not installed", APPS) is None
    assert pick_start_app("riotclient", [{"Name": "", "AppID": "x"}, {"AppID": "y"}, "bad", None]) is None


def test_resolve_app_uses_the_forgiving_match(monkeypatch):
    b = object.__new__(be.WindowsBackend)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return SimpleNamespace(returncode=0, stdout=json.dumps(APPS))
    monkeypatch.setattr(be.subprocess, "run", fake_run)
    assert b.resolve_app("riotclient") == "shell:AppsFolder\\C:\\Riot Games\\Riot Client\\RiotClientServices.exe"
    assert "riotclient" not in " ".join(seen["cmd"])                  # the name is never put in the command line
    assert b.resolve_app("") is None
    monkeypatch.setattr(be.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert b.resolve_app("riotclient") is None                        # PowerShell failing is a clean 'not found'
    monkeypatch.setattr(be.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="not json"))
    assert b.resolve_app("riotclient") is None
