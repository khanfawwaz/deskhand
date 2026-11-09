"""OS-specific desktop operations, isolated from the model and the executor logic."""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod

from ..protocol import Screenshot, WindowInfo


class Backend(ABC):
    @abstractmethod
    def screenshot(self, max_dimension: int) -> Screenshot: ...
    @abstractmethod
    def active_window(self) -> WindowInfo | None: ...
    @abstractmethod
    def launch(self, target: str) -> int | None: ...
    @abstractmethod
    def process_status(self, target: str | None, pid: int | None, hint: str | None = None) -> dict: ...
    @abstractmethod
    def click(self, x: int, y: int, button: str, clicks: int) -> None: ...
    @abstractmethod
    def type_text(self, text: str) -> None: ...
    @abstractmethod
    def press(self, keys: list[str]) -> None: ...
    @abstractmethod
    def scroll(self, x: int, y: int, clicks: int) -> None: ...
    @abstractmethod
    def display_size(self) -> tuple[int, int]: ...

    # Optional capabilities used by registry tools. Not abstract so minimal backends keep working.
    def list_windows(self) -> list[dict]:
        raise NotImplementedError

    def focus_window(self, title: str) -> dict:
        raise NotImplementedError

    def get_clipboard(self) -> str:
        raise NotImplementedError

    def set_clipboard(self, text: str) -> None:
        raise NotImplementedError

    def open_url(self, url: str) -> None:
        raise NotImplementedError

    def launch_cli(self, exe: str, args: list[str]) -> int | None:
        raise NotImplementedError

    # UI Automation (read controls by name) and zoomed screenshots
    def ui_window(self, title: str) -> dict | None:
        raise NotImplementedError

    def ui_snapshot(self, win: dict, max_elements: int, query: str, name_chars: int = 90, role: str = "",
                    skip: int = 0, min_chars: int = 0) -> dict:
        raise NotImplementedError

    def ui_act(self, win: dict, sel: dict, action: str, text: str | None) -> dict:
        raise NotImplementedError

    def ui_wait(self, win: dict, sel: dict, timeout_s: float, gone: bool) -> dict:
        raise NotImplementedError

    def ui_read_page(self, win: dict, min_wait_s: float, quiet_s: float, max_wait_s: float, max_chars: int) -> dict:
        raise NotImplementedError

    def screenshot_region(self, x: int, y: int, w: int, h: int, max_dimension: int, upscale: bool = True) -> Screenshot:
        raise NotImplementedError

    def screenshot_window(self, max_dimension: int) -> Screenshot:
        """Only the foreground window's pixels (privacy mode)."""
        raise NotImplementedError

    def window_host(self, title: str) -> str | None:
        """Host (e.g. 'chatgpt.com') shown in a browser window's address bar, or None if unknown."""
        from urllib.parse import urlparse
        u = self.window_url(title)
        return urlparse(u).hostname if u else None

    def window_url(self, title: str) -> str | None:
        """Full address (with https://) shown in a browser window's address bar, or None if unknown."""
        return None

    def resolve_app(self, name: str) -> str | None:
        return None

    def open_private(self, url: str) -> dict:
        """Open a URL in a private/incognito browser window (no logged-in account data)."""
        raise NotImplementedError


class WindowsBackend(Backend):
    """mss for capture, pyautogui for input, psutil/ctypes for processes and windows.

    Heavy imports are deferred so the module can be imported (and tested) anywhere.
    """

    def __init__(self) -> None:
        import pyautogui
        pyautogui.FAILSAFE = True   # slam the mouse into a corner = emergency abort
        pyautogui.PAUSE = 0.05
        self.pg = pyautogui
        self._launched: dict[str, int] = {}
        try:  # make coordinates physical pixels so they match captures
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    def display_size(self) -> tuple[int, int]:
        import mss
        with mss.mss() as s:
            m = s.monitors[1]
            return m["width"], m["height"]

    def screenshot(self, max_dimension: int) -> Screenshot:
        import mss
        from PIL import Image
        with mss.mss() as s:
            m = s.monitors[1]
            raw = s.grab(m)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        dw, dh = img.size
        scale = max(1.0, max(dw, dh) / max_dimension)
        if scale > 1.0:
            img = img.resize((round(dw / scale), round(dh / scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        dpi = 1.0
        try:
            import ctypes
            dpi = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100
        except Exception:
            pass
        return Screenshot(
            image_b64=base64.b64encode(buf.getvalue()).decode(), width=img.width, height=img.height,
            display_width=dw, display_height=dh, scale=scale, dpi_scale=dpi, monitor=1,
            captured_at=time.time(), active_window=self.active_window())

    def active_window(self) -> WindowInfo | None:
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = ""
            try:
                import psutil
                name = psutil.Process(pid.value).name()
            except Exception:
                pass
            return WindowInfo(title=buf.value, pid=pid.value, process=name)
        except Exception:
            return None

    def resolve_app(self, name: str) -> str | None:
        """Find an installed app by (part of) its Start-menu name. Returns something launch() accepts, or None.
        Uses Windows' own Start-menu index (covers classic and Store apps); the name is never put in a command line
        unescaped (it is passed as an environment variable)."""
        import json
        clean = re.sub(r"[^\w .+\-]", "", name or "")[:60].strip()
        if not clean:
            return None
        script = ("$n = $env:DESKHAND_APP_NAME; Get-StartApps | Where-Object { $_.Name -like \"*$n*\" } | "
                  "Select-Object -First 8 | ConvertTo-Json -Compress")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                           timeout=25, env={**os.environ, "DESKHAND_APP_NAME": clean}, creationflags=0x08000000)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            apps = json.loads(r.stdout)
        except ValueError:
            return None
        apps = apps if isinstance(apps, list) else [apps]
        apps = [a for a in apps if isinstance(a, dict) and a.get("AppID") and a.get("Name")]
        if not apps:
            return None
        best = next((a for a in apps if a["Name"].lower() == clean.lower()), None) or min(apps, key=lambda a: len(a["Name"]))
        return "shell:AppsFolder\\" + best["AppID"]

    def launch(self, target: str) -> int | None:
        # `target` always comes from the local approved_apps table, never from the model.
        if target.lower().startswith("shell:"):                       # a Start-menu app (classic or Store)
            subprocess.Popen(["explorer.exe", target], shell=False, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        p = subprocess.Popen([target], shell=False, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._launched[os.path.basename(target).lower()] = p.pid
        return p.pid

    def _visible_window_pids(self) -> set[int]:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        pids: set[int] = set()
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) > 0:
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pids.add(pid.value)
            return True
        u.EnumWindows(EnumProc(cb), 0)
        return pids

    def process_status(self, target: str | None, pid: int | None, hint: str | None = None) -> dict:
        import psutil
        exe = os.path.basename(target).lower() if target else None
        win_pids = self._visible_window_pids()
        matches = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if (pid and p.info["pid"] == pid) or (exe and p.info["name"].lower() == exe):
                    matches.append(p.info["pid"])
            except Exception:
                continue
        # Store apps (Calculator, Settings...) are started by a stub exe that exits at once and then live in
        # another process: fall back to a visible window whose TITLE contains the app id.
        if not matches and hint:
            matches = [w[3] for w in self._windows() if hint.lower() in w[1].lower()]
        # Modern apps (e.g. Notepad) hand off to a different process; match by name, not pid.
        title = next((w[1] for w in self._windows() if w[3] in matches and w[3] in win_pids), "")
        return {"running": bool(matches), "pid": matches[0] if matches else None,
                "window_visible": any(m in win_pids for m in matches), "title": title}

    def screenshot_window(self, max_dimension: int) -> Screenshot:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        r = wintypes.RECT()
        u.GetWindowRect(u.GetForegroundWindow(), ctypes.byref(r))
        return self.screenshot_region(r.left, r.top, r.right - r.left, r.bottom - r.top, max_dimension, upscale=False)

    def window_url(self, title: str) -> str | None:
        """Full address shown in a browser window's address bar (the toolbar's 'Address and search bar'), or None."""
        from . import uia
        hit = next((w for w in self._windows() if w[1] == title), None)
        if hit is None:
            return None
        with uia._Com():
            root = uia._auto().ControlFromHandle(hit[0])
            for e in uia._walk(root, True, limit=400):          # the toolbar comes long before the page
                if e["role"] == "Edit" and "address and search bar" in e["name"].lower():
                    try:
                        url = str(e["ctrl"].GetValuePattern().Value).strip()
                    except Exception:
                        return None
                    return url if "://" in url else "https://" + url
        return None

    def _focus_hwnd(self, hwnd: int) -> bool:
        import ctypes
        import time
        u = ctypes.windll.user32
        u.keybd_event(0x12, 0, 0, 0)      # tapping ALT lifts Windows' foreground-lock restriction
        u.keybd_event(0x12, 0, 2, 0)
        u.ShowWindow(hwnd, 9)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return u.GetForegroundWindow() == hwnd

    def open_private(self, url: str) -> dict:
        """Open `url` in a private window, wait for THAT window to appear and bring it to the front, so the next
        observation shows it (a background window would be refused by privacy mode)."""
        import shutil
        import time
        local = os.environ.get("LOCALAPPDATA", "")
        browsers = [
            ("chrome", "chrome.exe", "--incognito", [shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                                                    os.path.join(local, r"Google\Chrome\Application\chrome.exe")]),
            ("edge", "msedge.exe", "--inprivate", [shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]),
        ]
        for name, exe, flag, paths in browsers:
            for p in paths:
                if p and os.path.exists(p):
                    before = {w[0] for w in self._windows() if w[2].lower() == exe}
                    subprocess.Popen([p, flag, "--new-window", url], shell=False, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    focused = False
                    for _ in range(30):                       # up to ~9 s for the new window
                        time.sleep(0.3)
                        new = [w for w in self._windows() if w[2].lower() == exe and w[0] not in before]
                        if new:
                            focused = self._focus_hwnd(new[0][0])
                            break
                    return {"browser": name, "private": True, "focused": focused}
        raise FileNotFoundError("no Chrome or Edge installation found")

    def screenshot_region(self, x: int, y: int, w: int, h: int, max_dimension: int, upscale: bool = True) -> Screenshot:
        """Crop of the display, ENLARGED so small controls become legible. origin/scale let the
        orchestrator map clicks made on this crop back to real display coordinates."""
        import mss
        from PIL import Image
        with mss.MSS() as s:
            m = s.monitors[1]
            dw, dh = m["width"], m["height"]
            x, y = max(0, min(x, dw - 16)), max(0, min(y, dh - 16))
            w, h = max(16, min(w, dw - x)), max(16, min(h, dh - y))
            raw = s.grab({"left": m["left"] + x, "top": m["top"] + y, "width": w, "height": h})
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        up = min(4.0 if upscale else 1.0, max_dimension / max(w, h))   # enlarge (zoom) up to 4x; window crops never
        if up != 1.0:
            img = img.resize((max(1, round(w * up)), max(1, round(h * up))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return Screenshot(
            image_b64=base64.b64encode(buf.getvalue()).decode(), width=img.width, height=img.height,
            display_width=dw, display_height=dh, scale=w / img.width, origin_x=x, origin_y=y,
            captured_at=time.time(), active_window=self.active_window())

    def ui_window(self, title: str) -> dict | None:
        from . import uia
        return uia.find_window(title)

    def ui_snapshot(self, win: dict, max_elements: int, query: str, name_chars: int = 90, role: str = "",
                    skip: int = 0, min_chars: int = 0) -> dict:
        from . import uia
        return uia.snapshot(win, max_elements, query, name_len=name_chars, role=role, skip=skip, min_chars=min_chars)

    def ui_act(self, win: dict, sel: dict, action: str, text: str | None) -> dict:
        from . import uia
        return uia.act(win, sel, action, text)

    def ui_wait(self, win: dict, sel: dict, timeout_s: float, gone: bool) -> dict:
        from . import uia
        return uia.wait_for(win, sel, timeout_s, gone)

    def ui_read_page(self, win: dict, min_wait_s: float, quiet_s: float, max_wait_s: float, max_chars: int) -> dict:
        from . import uia
        return uia.page_text(win, min_wait_s, quiet_s, max_wait_s, max_chars)

    def _windows(self) -> list[tuple[int, str, str, int]]:
        """(hwnd, title, process, pid) of visible, titled top-level windows."""
        import ctypes
        from ctypes import wintypes
        import psutil
        u = ctypes.windll.user32
        out: list[tuple[int, str, str, int]] = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            n = u.GetWindowTextLengthW(hwnd)
            if u.IsWindowVisible(hwnd) and n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    name = psutil.Process(pid.value).name()
                except Exception:
                    name = ""
                out.append((hwnd, buf.value, name, pid.value))
            return True
        u.EnumWindows(EnumProc(cb), 0)
        return out

    def list_windows(self) -> list[dict]:
        return [{"title": t, "process": p, "pid": pid} for _, t, p, pid in self._windows()]

    def focus_window(self, title: str) -> dict:
        import ctypes
        import time
        u = ctypes.windll.user32
        from ..titles import title_has
        match = next((w for w in self._windows() if title_has(w[1], title)), None)
        if match is None:
            return {"focused": False, "active_title": (self.active_window() or WindowInfo()).title,
                    "error": f"no visible window with '{title}' in its title"}
        hwnd = match[0]
        u.keybd_event(0x12, 0, 0, 0)      # tapping ALT lifts Windows' foreground-lock restriction
        u.keybd_event(0x12, 0, 2, 0)
        u.ShowWindow(hwnd, 9)             # SW_RESTORE (un-minimize)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        w = self.active_window()
        return {"focused": bool(w) and title_has(w.title, title), "active_title": w.title if w else ""}

    def get_clipboard(self) -> str:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.rstrip("\r\n")

    def set_clipboard(self, text: str) -> None:
        cmd = ("Set-Clipboard -Value $input" if text else
               "Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.Clipboard]::Clear()")
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], input=text, text=True,
                       timeout=15, check=True)

    def open_url(self, url: str) -> None:
        os.startfile(url)  # type: ignore[attr-defined]   # default handler; url pre-validated by policy

    def launch_cli(self, exe: str, args: list[str]) -> int | None:
        import shutil
        path = shutil.which(exe)
        if path is None:
            raise FileNotFoundError(f"'{exe}' not found on PATH")
        return subprocess.Popen([path, *args], shell=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid

    def click(self, x, y, button, clicks):
        self.pg.click(x, y, clicks=clicks, button=button)

    def type_text(self, text):
        self.pg.write(text, interval=0.01)

    def press(self, keys):
        self.pg.press(keys[0]) if len(keys) == 1 else self.pg.hotkey(*keys)

    def scroll(self, x, y, clicks):
        self.pg.moveTo(x, y)
        self.pg.scroll(clicks)
