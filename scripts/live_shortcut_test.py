"""Live check, on THIS Windows computer, of the "Change shortcut" box in the Deskhand app.

    python scripts/live_shortcut_test.py

Uses the real app window code with a throw-away settings folder (your own settings are not touched). It opens the box, picks a
new shortcut, saves it, and checks with real key presses that the new one works and the old one no longer does; then it tries
a shortcut another program already owns and checks the box refuses it and keeps the old one.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["DESKHAND_DEV"] = "shortcuttest"                       # its own window class: never collides with an installed Deskhand

from taskpilot.agent import app as appmod, hotkey              # noqa: E402

u = ctypes.windll.user32
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def pump(seconds: float, want_hotkey: bool = False) -> bool:
    """Run Windows' messages for a moment. True if our shortcut message arrived."""
    got = False
    m = wintypes.MSG()
    end = time.time() + seconds
    while time.time() < end:
        if u.PeekMessageW(ctypes.byref(m), None, 0, 0, 1):
            if m.message == appmod.WM_HOTKEY and m.wParam == appmod.HOTKEY_ID:
                got = True
            u.TranslateMessage(ctypes.byref(m))
            u.DispatchMessageW(ctypes.byref(m))
        else:
            time.sleep(0.01)
    return got


def press(*vks: int) -> None:
    for vk in vks:
        u.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        u.keybd_event(vk, 0, 2, 0)


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="deskhand_shortcut_"))
    (home / "agent.json").write_text(json.dumps({"server_url": "wss://x/agent/ws", "device_id": "d", "policy": {"cloud_safe": True}}), encoding="utf-8")
    app = appmod.DeskhandApp(home, minimized=True, show=False)
    app.hotkey_ok, app.hotkey_msg = hotkey.register(app.hwnd, appmod.HOTKEY_ID, hotkey.read_hotkey(home))
    check("starts with Alt+F8", app.hotkey_ok, app.hotkey_msg)
    press(0x12, 0x77)
    check("Alt+F8 works", pump(1.0))

    app.change_shortcut()
    pump(0.3)
    check("the box opened", bool(app.kdlg and u.IsWindow(app.kdlg)))
    check("the box shows the current shortcut", (u.SendMessageW(app.khot, 0x402, 0, 0) & 0xFFFF) == hotkey.to_control("alt+f8"))
    press(0x12, 0x77)
    check("while the box is open Alt+F8 goes to the box, not to the prompt", not pump(0.8))

    # pick Ctrl+Alt+V in the box and press Save
    u.SendMessageW(app.khot, 0x401, hotkey.to_control("ctrl+alt+v"), 0)
    u.SendMessageW(app.kdlg, appmod.WM_COMMAND, appmod.ID_SAVE, 0)
    pump(0.3)
    check("Save closed the box", app.kdlg is None)
    check("the new shortcut is remembered in the settings file", hotkey.read_hotkey(home) == "ctrl+alt+v", hotkey.read_hotkey(home))
    check("other settings in the file are untouched", json.loads((home / "agent.json").read_text(encoding="utf-8")).get("policy") == {"cloud_safe": True})
    press(0x11, 0x12, ord("V"))
    check("Ctrl+Alt+V now works", pump(1.0))
    press(0x12, 0x77)
    check("and Alt+F8 no longer does", not pump(0.8))

    # a shortcut another program already owns is refused, and the current one stays
    other = u.RegisterHotKey(None, 0x5151, 0x4000 | hotkey.MOD_CONTROL | hotkey.MOD_ALT, ord("B"))
    check("(set-up) another program owns Ctrl+Alt+B", bool(other))
    app.change_shortcut()
    pump(0.3)
    u.SendMessageW(app.khot, 0x401, hotkey.to_control("ctrl+alt+b"), 0)
    u.SendMessageW(app.kdlg, appmod.WM_COMMAND, appmod.ID_SAVE, 0)
    pump(0.3)
    check("the box refuses a shortcut that is already taken and stays open", bool(app.kdlg and u.IsWindow(app.kdlg)))
    u.SendMessageW(app.kdlg, appmod.WM_COMMAND, appmod.ID_CANCEL, 0)
    pump(0.3)
    check("Cancel puts the previous shortcut back", app.hotkey_ok and hotkey.read_hotkey(home) == "ctrl+alt+v")
    press(0x11, 0x12, ord("V"))
    check("...and it works", pump(1.0))

    # turning it off
    app.change_shortcut()
    pump(0.3)
    u.SendMessageW(app.kdlg, appmod.WM_COMMAND, appmod.ID_OFF, 0)
    pump(0.3)
    check("'Turn shortcut off' switches it off and remembers that", hotkey.read_hotkey(home) == "off" and not app.hotkey_ok)

    u.UnregisterHotKey(None, 0x5151)
    hotkey.unregister(app.hwnd, appmod.HOTKEY_ID)
    u.DestroyWindow(app.hwnd)
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
