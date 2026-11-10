"""The global shortcut that brings up the voice prompt (Alt+F8 by default).

Pure parsing lives here so it can be tested anywhere; the actual registration is one call to Windows' RegisterHotKey.
"""
from __future__ import annotations

import json
from pathlib import Path

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
DEFAULT = "alt+f8"
_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN}
_NAMED = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "insert": 0x2D,
          "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pause": 0x13, "scrolllock": 0x91,
          "printscreen": 0x2C}


class BadHotkey(ValueError):
    pass


def parse_hotkey(text: str) -> tuple[int, int]:
    """'alt+f8' -> (modifier flags, virtual-key code). Raises BadHotkey with a plain message."""
    parts = [p.strip().lower() for p in str(text or "").replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise BadHotkey("no shortcut given")
    key, mods = parts[-1], 0
    for m in parts[:-1]:
        if m not in _MODS:
            raise BadHotkey(f"'{m}' is not a modifier (use alt, ctrl, shift or win)")
        mods |= _MODS[m]
    if key in _MODS:
        raise BadHotkey("a shortcut needs a key as well as a modifier")
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key in _NAMED:
        vk = _NAMED[key]
    else:
        raise BadHotkey(f"'{key}' is not a key I know")
    if not mods and not (key.startswith("f") and key[1:].isdigit()):
        raise BadHotkey("use at least one modifier (alt, ctrl, shift or win) with that key, or a function key")
    return mods, vk


def describe(text: str) -> str:
    return "+".join(w.capitalize() if len(w) > 1 else w.upper() for w in str(text).lower().replace(" ", "").split("+"))


def read_hotkey(home: Path) -> str:
    """The shortcut from this computer's settings file (agent.json: "hotkey"), or the default."""
    try:
        cfg = json.loads((home / "agent.json").read_text(encoding="utf-8-sig"))
        value = cfg.get("hotkey", DEFAULT)
        return DEFAULT if value is None else str(value)
    except (OSError, ValueError):
        return DEFAULT


def register(hwnd: int, hotkey_id: int, text: str) -> tuple[bool, str]:
    """Ask Windows to send this window a WM_HOTKEY when the shortcut is pressed anywhere. -> (worked, plain message)."""
    import ctypes
    if not str(text or "").strip() or str(text).lower() in ("off", "none", "disabled"):
        return False, "The voice shortcut is switched off."
    try:
        mods, vk = parse_hotkey(text)
    except BadHotkey as e:
        return False, f"The voice shortcut '{text}' is not valid: {e}."
    if ctypes.windll.user32.RegisterHotKey(hwnd, hotkey_id, mods | MOD_NOREPEAT, vk):
        return True, f"Press {describe(text)} and say what to do."
    return False, f"{describe(text)} is already used by another program. Change it with \"hotkey\" in Deskhand's settings file."


def unregister(hwnd: int, hotkey_id: int) -> None:
    import ctypes
    try:
        ctypes.windll.user32.UnregisterHotKey(hwnd, hotkey_id)
    except Exception:
        pass


# ---------------------------------------------------------------- the 'press the keys you want' box (Windows' own hot-key control)
HOTKEYF_SHIFT, HOTKEYF_CONTROL, HOTKEYF_ALT = 0x01, 0x02, 0x04
_VK_NAMES = {v: k for k, v in {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08, "insert": 0x2D,
                               "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
                               "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C}.items()}


def to_control(text: str) -> int:
    """A shortcut as the value the hot-key control shows (virtual key in the low byte, modifiers in the high byte)."""
    mods, vk = parse_hotkey(text)
    if mods & MOD_WIN:
        raise BadHotkey("the Windows key cannot be chosen in the box; edit \"hotkey\" in the settings file instead")
    flags = (HOTKEYF_ALT if mods & MOD_ALT else 0) | (HOTKEYF_CONTROL if mods & MOD_CONTROL else 0) | (HOTKEYF_SHIFT if mods & MOD_SHIFT else 0)
    return (flags << 8) | vk


def from_control(value: int) -> str:
    """What the person pressed in the box -> 'ctrl+alt+k'. Raises BadHotkey (with a plain message) if it cannot be used."""
    vk, flags = value & 0xFF, (value >> 8) & 0xFF
    if not vk:
        raise BadHotkey("press the keys you want first")
    parts = [n for f, n in ((HOTKEYF_CONTROL, "ctrl"), (HOTKEYF_ALT, "alt"), (HOTKEYF_SHIFT, "shift")) if flags & f]
    if 0x70 <= vk <= 0x87:
        key = f"f{vk - 0x6F}"
    elif chr(vk).isalnum() and vk < 0x80:
        key = chr(vk).lower()
    elif vk in _VK_NAMES:
        key = _VK_NAMES[vk]
    else:
        raise BadHotkey("that key cannot be used")
    text = "+".join(parts + [key])
    parse_hotkey(text)                                   # the same rules as the settings file: modifier needed, etc.
    return text


def write_hotkey(home: Path, text: str) -> None:
    """Save the shortcut into this computer's settings file, keeping everything else in it as it was."""
    path = home / "agent.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    cfg["hotkey"] = text
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")     # no byte-order mark: written whole, then swapped in
    tmp.replace(path)
