"""The extension bridge: a local server the browser extension connects to (127.0.0.1) after the user approves it once."""
import json
import socket
import threading

import pytest

from taskpilot.agent import extbridge
from taskpilot.agent.cdp import BridgeError, ExtPage

EXT = "abcdefghijklmnopabcdefghijklmnop"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def connect(port, query, origin=f"chrome-extension://{EXT}"):
    from websockets.sync.client import connect as ws_connect
    return ws_connect(f"ws://127.0.0.1:{port}/?{query}", origin=origin) if origin else ws_connect(f"ws://127.0.0.1:{port}/?{query}")


def serve_answers(ws, answers):
    def loop():
        try:
            for raw in ws:
                m = json.loads(raw)
                if "cmd" not in m:
                    continue
                fn = answers.get(m.get("method") or m["cmd"])
                ws.send(json.dumps({"id": m["id"], "error": "unknown"} if fn is None else {"id": m["id"], "result": fn(m)}))
        except Exception:
            pass
    threading.Thread(target=loop, daemon=True).start()


def make_bridge(tmp_path, approve=lambda ext: True):
    b = extbridge.ExtBridge(port=free_port(), approve=approve, paired_file=tmp_path / "paired.json")
    b.start()
    return b


def pair(b):
    ws = connect(b.port, "pair=1")
    msg = json.loads(ws.recv(timeout=5))
    return ws, msg


def test_pairing_needs_approval_then_the_extension_can_connect_and_answer(tmp_path):
    asked = []
    b = make_bridge(tmp_path, approve=lambda ext: asked.append(ext) or True)
    ws, msg = pair(b)
    token = msg["paired"]
    assert asked == [EXT] and b.wait_connected(3)
    serve_answers(ws, {"tabs": lambda m: [{"id": 7, "url": "https://www.youtube.com/", "active": True}],
                       "Runtime.evaluate": lambda m: {"result": {"value": "https://www.youtube.com/"}}})
    assert b.request({"cmd": "tabs"})[0]["id"] == 7
    assert ExtPage(b, 7).url() == "https://www.youtube.com/"             # the same Page helpers work over the extension
    ws.close()
    # later: reconnect with the token, no question asked
    asked.clear()
    ws2 = connect(b.port, f"token={token}")
    assert json.loads(ws2.recv(timeout=5)) == {"welcome": True} and asked == []
    ws2.close()
    b.stop()


def test_a_declined_pairing_gives_no_token_and_no_connection(tmp_path):
    b = make_bridge(tmp_path, approve=lambda ext: False)
    ws, msg = pair(b)
    assert "denied" in msg and "paired" not in msg and not b.connected
    assert not (tmp_path / "paired.json").exists()
    b.stop()


def test_a_wrong_or_stolen_token_is_refused(tmp_path):
    b = make_bridge(tmp_path)
    ws, msg = pair(b)
    token = msg["paired"]
    ws.close()
    assert json.loads(connect(b.port, "token=wrong").recv(timeout=5)).get("denied")
    # the right token from a DIFFERENT extension id is refused too
    other = connect(b.port, f"token={token}", origin="chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert json.loads(other.recv(timeout=5)).get("denied")
    b.stop()


def test_only_browser_extensions_may_connect(tmp_path):
    asked = []
    b = make_bridge(tmp_path, approve=lambda ext: asked.append(ext) or True)
    for origin in (None, "https://evil.example"):
        try:
            ws = connect(b.port, "pair=1", origin=origin)
            ws.recv(timeout=3)
        except Exception:
            pass
    assert asked == [] and not b.connected                               # a web page cannot even trigger the question
    b.stop()


def test_only_the_hash_of_the_token_is_stored(tmp_path):
    b = make_bridge(tmp_path)
    ws, msg = pair(b)
    saved = (tmp_path / "paired.json").read_text(encoding="utf-8")
    assert msg["paired"] not in saved and EXT in saved
    b.stop()


def test_errors_from_the_browser_surface_as_bridge_errors(tmp_path):
    b = make_bridge(tmp_path)
    ws, _ = pair(b)
    serve_answers(ws, {})
    with pytest.raises(BridgeError):
        ExtPage(b, 1).call("Page.navigate", {"url": "https://x.test"})
    b.stop()


def test_with_no_extension_a_request_fails_clearly(tmp_path):
    b = make_bridge(tmp_path)
    with pytest.raises(RuntimeError, match="not connected"):
        b.request({"cmd": "tabs"})
    b.stop()


def test_the_shipped_extension_is_store_ready_and_only_talks_locally():
    from pathlib import Path
    d = Path(__file__).resolve().parents[1] / "extension"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    bg = (d / "background.js").read_text(encoding="utf-8")
    assert man["manifest_version"] == 3 and set(man["permissions"]) == {"debugger", "tabs", "alarms", "storage"}
    assert "host_permissions" not in man and "cookies" not in man["permissions"]      # it cannot read cookies
    assert "ws://127.0.0.1" in bg and "wss://" not in bg and "fetch(" not in bg and "XMLHttpRequest" not in bg
    for n in (16, 32, 48, 128):
        assert (d / "icons" / f"icon{n}.png").is_file()
    assert len(man["description"]) <= 132 and man["name"] == "Deskhand"
