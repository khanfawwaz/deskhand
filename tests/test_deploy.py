"""The deployment files: the laptop behind the Oracle front door."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

D = Path(__file__).resolve().parents[1] / "deploy"


def test_nginx_site_has_a_port_placeholder_a_friendly_offline_page_and_hides_the_owner_views():
    conf = (D / "nginx-deskhand.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:__PORT__;" in conf and "__DOMAIN__" in conf
    assert "error_page 502 503 504" in conf and "offline for a moment" in conf and "proxy_connect_timeout 5s" in conf
    hidden = re.search(r"location ~ \^/\((.*?)\)\(/\|\$\) \{ return 404; \}", conf).group(1).split("|")
    assert {"usage", "pairing", "invites", "workspaces", "admin"} <= set(hidden)       # the owner's usage view is never public
    assert "proxy_buffering off" in conf and "Upgrade $http_upgrade" in conf             # live updates and the helper's WebSocket survive the hop


def test_the_tunnel_key_can_only_open_one_loopback_port_and_has_no_shell():
    sh = (D / "laptop" / "oracle-setup.sh").read_text(encoding="utf-8")
    assert "restrict,port-forwarding" in sh and 'permitlisten="127.0.0.1:%s"' in sh and "command=\"/bin/false\"" in sh
    unit = (D / "laptop" / "deskhand-tunnel.service").read_text(encoding="utf-8")
    assert "-R 127.0.0.1:18765:127.0.0.1:8765" in unit and "ExitOnForwardFailure=yes" in unit and "ServerAliveInterval" in unit
    assert "GatewayPorts" not in unit and "0.0.0.0" not in unit                          # never exposed beyond Oracle's own loopback


def test_the_laptop_server_listens_on_this_machine_only():
    unit = (D / "laptop" / "deskhand-server.service").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in unit and "0.0.0.0" not in unit
    ps = (D / "laptop" / "windows-install.ps1").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in ps and "-R 127.0.0.1:18765:127.0.0.1:8765" in ps


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_the_oracle_script_is_valid_shell():
    r = subprocess.run(["bash", "-n", str(D / "laptop" / "oracle-setup.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell parsing is checked on Windows")
def test_the_windows_script_parses():
    cmd = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e); "
           "if($e){$e|%%{$_.Message}; exit 1}" % (D / "laptop" / "windows-install.ps1"))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_env_example_lists_every_new_setting():
    env = (D / "laptop" / "laptop.env.example").read_text(encoding="utf-8")
    for k in ("TASKPILOT_MODEL_CONCURRENCY", "TASKPILOT_MODEL_PER_WORKSPACE", "TASKPILOT_MODEL_RPM", "TASKPILOT_PRICE_IN_PER_M",
              "TASKPILOT_PRICE_OUT_PER_M", "TASKPILOT_DAILY_BUDGET_USD", "TASKPILOT_DAILY_TOKEN_CAP", "TASKPILOT_WS_TOKENS_PER_DAY",
              "TASKPILOT_SIGNUP", "TASKPILOT_SMTP_USER", "TASKPILOT_SMTP_PASSWORD"):
        assert k in env, k
