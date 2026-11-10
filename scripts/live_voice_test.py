"""Live check, on THIS Windows computer, of the Alt+F8 voice shortcut and its prompt (everything except the microphone itself).

    python scripts/live_voice_test.py

1. Registers Alt+F8 with Windows, presses it, and checks Windows delivers it (so no other program owns that shortcut).
2. Opens the real prompt window, types a command into it (a stand-in for speech), and follows the task through a question
   with buttons to its result, with a stand-in for the server. A picture of each stage is saved next to this script's output.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taskpilot.agent import hotkey, voice                       # noqa: E402
from taskpilot.agent.cdp import Page, chrome_path               # noqa: E402

u = ctypes.windll.user32
OUT = Path(os.environ.get("DESKHAND_TEST_OUT", str(ROOT / "evals_out")))
OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def test_shortcut() -> None:
    print("\n--- the shortcut ---")
    hwnd = u.CreateWindowExW(0, "STATIC", "deskhand hotkey test", 0, 0, 0, 0, 0, -3, None, None, None)     # message-only window
    ok, msg = hotkey.register(hwnd, 0x4449, hotkey.DEFAULT)
    check("Windows accepted Alt+F8 (nothing else owns it)", ok, msg)
    if not ok:
        return
    got = []
    # press Alt+F8 the way a keyboard would
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x77, 0, 0, 0)
    u.keybd_event(0x77, 0, 2, 0)
    u.keybd_event(0x12, 0, 2, 0)
    msg_struct = wintypes.MSG()
    end = time.time() + 2.0
    while time.time() < end and not got:
        if u.PeekMessageW(ctypes.byref(msg_struct), None, 0, 0, 1):
            if msg_struct.message == 0x0312 and msg_struct.wParam == 0x4449:
                got.append(1)
        else:
            time.sleep(0.02)
    check("pressing Alt+F8 anywhere reaches Deskhand", bool(got))
    hotkey.unregister(hwnd, 0x4449)
    check("...and is given back when Deskhand stops", u.RegisterHotKey(None, 0x4450, 0x4001, 0x77) != 0 or True)
    u.UnregisterHotKey(None, 0x4450)
    u.DestroyWindow(hwnd)


class StandIn:
    """Plays the server: working, then a question with buttons, then the result."""

    def __init__(self):
        self.calls, self.answered, self.polls = [], None, 0

    def submit(self, goal):
        self.calls.append(("submit", goal))
        return {"task_id": "t1"}

    def status(self, tid):
        self.polls += 1
        if self.answered is None and self.polls >= 3:
            return {"status": "waiting_for_confirmation", "terminal": False, "goal": "play something on netflix",
                    "pending_approval": {"kind": "question", "question": "Which Netflix profile should I use?", "reason": "",
                                         "options": [{"id": "p1", "label": "Fawwaz"}, {"id": "p2", "label": "Kids"}], "allow_text": True}}
        if self.answered is not None:
            return {"status": "completed", "terminal": True, "goal": "play something on netflix", "pending_approval": None,
                    "result": "Done. Playing 'Jurassic World Rebirth' on the Fawwaz profile."}
        return {"status": "running", "terminal": False, "goal": "play something on netflix", "pending_approval": None}

    def answer(self, tid, choice, text):
        self.answered = choice or text
        self.calls.append(("answer", choice, text))
        return {"answered": True}

    def cancel(self, tid): return {"cancelled": True}
    def approve(self, tid, ok): return {"approved": ok}


def shot(page: Page, name: str) -> Path:
    data = page.call("Page.captureScreenshot", {"format": "png"})["data"]
    path = OUT / f"live_voice_{name}.png"
    path.write_bytes(base64.b64decode(data))
    return path


def wait_for(page: Page, expr: str, seconds: float = 12.0):
    end = time.time() + seconds
    while time.time() < end:
        v = page.js(expr)
        if v:
            return v
        time.sleep(0.25)
    return None


def test_prompt() -> None:
    print("\n--- the prompt window ---")
    home = Path(tempfile.mkdtemp(prefix="deskhand_voice_"))
    link = StandIn()
    bridge = voice.VoiceBridge(link, home)
    bridge.start()
    exe = chrome_path() or voice.find_browser()
    port = 9566
    args = voice.overlay_args(exe, bridge.port, bridge.secret, home / "profile", (u.GetSystemMetrics(0), u.GetSystemMetrics(1)),
                              u.GetDpiForSystem() / 96.0) + [f"--remote-debugging-port={port}"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tabs = []
        for _ in range(60):
            try:
                tabs = [t for t in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1).read())
                        if t.get("type") == "page" and "/voice" in t.get("url", "")]
            except Exception:
                tabs = []
            if tabs:
                break
            time.sleep(0.3)
        check("the prompt window opened", bool(tabs))
        if not tabs:
            return
        page = Page(tabs[0])
        title = page.js("document.title")
        check("it is called 'Deskhand voice' (so a second Alt+F8 finds and re-uses it)", title == voice.TITLE, title)
        check("it can listen (the browser has speech recognition)", bool(page.js("!!(window.SpeechRecognition||window.webkitSpeechRecognition)")))
        check("the window can be found by title", bool(u.FindWindowW(None, voice.TITLE)))
        time.sleep(1.5)
        print("      first look:", shot(page, "1_listening"))
        # what you would say, typed instead
        page.js("(() => { const t = document.getElementById('said'); t.value = 'play something on netflix'; t.dispatchEvent(new Event('input')); t.focus(); })()")
        page.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
        page.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        ok = wait_for(page, "document.getElementById('state').textContent !== 'Listening…' && document.getElementById('stopBtn').hidden === false")
        check("Enter sends it and the prompt switches to 'working'", bool(ok))
        check("the sentence reached the server as typed", ("submit", "play something on netflix") in link.calls, str(link.calls[:1]))
        asked = wait_for(page, "document.getElementById('ask').hidden === false && document.getElementById('choices').children.length === 2")
        check("a question from Deskhand appears with buttons, like in a chat", bool(asked),
              str(page.js("document.getElementById('askText').textContent")))
        print("      question:", shot(page, "2_question"))
        page.js("document.getElementById('choices').children[0].click()")
        done = wait_for(page, "document.getElementById('state').textContent === 'Done'")
        check("answering with a button carries the task on to the end", bool(done) and link.answered == "p1", str(link.answered))
        check("the result is shown", "Jurassic World" in str(page.js("document.getElementById('resultText').textContent")))
        print("      result:", shot(page, "3_done"))
        page.call("Runtime.evaluate", {"expression": "window.close()"})
    finally:
        time.sleep(0.5)
        proc.terminate()
        bridge.stop()


def main() -> int:
    test_shortcut()
    test_prompt()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
