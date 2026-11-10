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


def _norm_app(s: str) -> str:
    """'Riot Client', 'riot-client' and 'riotclient' are the same app name."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def pick_start_app(query: str, apps: list[dict]) -> dict | None:
    """The Start-menu entry that best matches a name the model or the person used. Forgiving about spaces, case and
    punctuation (Windows lists 'Riot Client', the model says 'riotclient'). Exact beats prefix beats contains; ties go
    to the shortest name, so 'notepad' picks Notepad, not Notepad++."""
    q = _norm_app(query)
    if not q:
        return None
    ranked = []
    for a in apps:
        if not (isinstance(a, dict) and a.get("AppID") and a.get("Name")):
            continue
        n = _norm_app(a["Name"])
        if not n:
            continue
        if a["Name"].strip().lower() == (query or "").strip().lower():
            rank = -1                               # the exact name, symbols and all ("Notepad", not "Notepad++")
        elif n == q:
            rank = 0
        elif n.startswith(q):
            rank = 1
        elif q in n:
            rank = 2
        elif len(n) >= 4 and n in q:                # 'riotclientservices' asked for, 'Riot Client' installed
            rank = 3
        else:
            continue
        ranked.append((rank, len(n), a))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (x[0], x[1]))
    return ranked[0][2]


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

    def web(self):
        """The browser bridge (web.* tools), or None when this computer has none."""
        return None

    # Sharing the computer with its owner (see agent/polite.py). Minimal backends do nothing here.
    polite = None                                   # set by the executor

    def user_idle_s(self) -> float | None:
        """Seconds since the PERSON last used the mouse or keyboard, or None when unknown."""
        return None

    def capture_user_context(self) -> dict | None:
        """What the person has in front and where their mouse is, so it can be put back."""
        return None

    def restore_user_context(self, ctx: dict) -> None:
        return None

    def screenshot_window_titled(self, title: str, max_dimension: int) -> Screenshot:
        """Capture one window even when other windows are on top of it. Not every backend can."""
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
        if not _norm_app(name):
            return None
        # list every Start-menu app and match here: the name is never put in a command line, and spaces/case do not matter
        script = "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                           timeout=40, creationflags=0x08000000)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            apps = json.loads(r.stdout)
        except ValueError:
            return None
        best = pick_start_app(name, apps if isinstance(apps, list) else [apps])
        return ("shell:AppsFolder\\" + best["AppID"]) if best else None

    def launch(self, target: str) -> int | None:
        # `target` always comes from the local approved_apps table, never from the model.
        keep = self._begin_background_launch()
        if target.lower().startswith("shell:"):                       # a Start-menu app (classic or Store)
            subprocess.Popen(["explorer.exe", target], shell=False, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._finish_background_launch(keep)
            return None
        p = subprocess.Popen([target], shell=False, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._launched[os.path.basename(target).lower()] = p.pid
        self._finish_background_launch(keep)
        return p.pid

    # ---- a launched app opens BEHIND what the person is doing, instead of on top of it
    def _begin_background_launch(self):
        pol = self.polite
        if pol is None or not pol.enabled:
            return None
        idle = self.user_idle_s()
        if idle is not None and idle >= 10:                          # nobody is using the computer: no need to hide anything
            return None
        return {"ctx": self.capture_user_context(), "before": {w[0] for w in self._windows()}}

    def _finish_background_launch(self, keep) -> None:
        if not keep:
            return
        import threading
        threading.Thread(target=self._tuck_away, args=(keep,), daemon=True, name="deskhand-tuck").start()

    def _tuck_away(self, keep: dict) -> None:
        import ctypes
        import time
        u = ctypes.windll.user32
        mine: set[int] = set()
        end = time.monotonic() + 9.0
        while time.monotonic() < end:
            for hwnd, _title, _proc, _pid in self._windows():
                if hwnd not in keep["before"] and hwnd not in mine:
                    mine.add(hwnd)
                    u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_BOTTOM, no move, no resize, no activate
            if mine:
                ctx = keep.get("ctx") or {}
                fg = u.GetForegroundWindow()
                if ctx.get("hwnd") and fg in mine and u.IsWindow(ctx["hwnd"]):      # the new app grabbed focus: give it back
                    self._activate_hwnd(ctx["hwnd"], allow_alt=False)
                if time.monotonic() > end - 7.0:
                    return
            time.sleep(0.25)

    def user_idle_s(self) -> float | None:
        try:
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            now = ctypes.windll.kernel32.GetTickCount()
            return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
        except Exception:
            return None

    def capture_user_context(self) -> dict | None:
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            return {"hwnd": u.GetForegroundWindow(), "cursor": (pt.x, pt.y)}
        except Exception:
            return None

    def _activate_hwnd(self, hwnd: int, allow_alt: bool = True) -> bool:
        """Bring a window to the front. First by sharing the front window's input queue (no keystroke is sent, so a person
        typing in that window notices nothing); tapping ALT is only the last resort, and only when nobody is typing."""
        import ctypes
        import time
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
        fg = u.GetForegroundWindow()
        me = k.GetCurrentThreadId()
        fg_thread = u.GetWindowThreadProcessId(fg, None)
        attached = bool(fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True))
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u.AttachThreadInput(me, fg_thread, False)
        time.sleep(0.12)
        if u.GetForegroundWindow() != hwnd and allow_alt:
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(hwnd)
            time.sleep(0.12)
        return u.GetForegroundWindow() == hwnd

    def restore_user_context(self, ctx: dict) -> None:
        import ctypes
        u = ctypes.windll.user32
        hwnd = ctx.get("hwnd")
        if hwnd and u.IsWindow(hwnd) and u.GetForegroundWindow() != hwnd:
            idle = self.user_idle_s()
            self._activate_hwnd(hwnd, allow_alt=idle is None or idle >= 2.5)
        if ctx.get("cursor"):
            u.SetCursorPos(int(ctx["cursor"][0]), int(ctx["cursor"][1]))

    def screenshot_window_titled(self, title: str, max_dimension: int) -> Screenshot:
        """Capture a window by asking Windows to paint it into a bitmap (PrintWindow): works while other windows cover it
        and never needs it to be brought to the front, so nothing else on the screen can end up in the picture."""
        from ..titles import title_has
        hit = next((w for w in self._windows() if title_has(w[1], title)), None)
        if hit is None:
            raise RuntimeError(f"no visible window with '{title}' in its title")
        return self.screenshot_hwnd(hit[0], max_dimension)

    def screenshot_hwnd(self, hwnd: int, max_dimension: int) -> Screenshot:
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        u.GetWindowDC.restype = wintypes.HDC
        u.GetWindowDC.argtypes = [wintypes.HWND]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteDC.argtypes = [wintypes.HDC]
        u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0 or u.IsIconic(hwnd):
            raise RuntimeError("that window is minimised or has no size, so it cannot be captured")
        hdc = u.GetWindowDC(hwnd)
        mdc = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        old = g.SelectObject(mdc, bmp)
        try:
            if not u.PrintWindow(hwnd, mdc, 2):                      # 2 = PW_RENDERFULLCONTENT (also modern and Chromium windows)
                raise RuntimeError("Windows could not paint that window into a picture")

            class BMIH(ctypes.Structure):
                _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                            ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                            ("biSizeImage", ctypes.c_uint32), ("biXPels", ctypes.c_int32), ("biYPels", ctypes.c_int32),
                            ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]
            bi = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)       # top-down 32-bit
            buf = ctypes.create_string_buffer(w * h * 4)
            if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
                raise RuntimeError("could not read the window's picture")
            img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        finally:
            g.SelectObject(mdc, old)
            g.DeleteObject(bmp)
            g.DeleteDC(mdc)
            u.ReleaseDC(hwnd, hdc)
        if img.getextrema() == ((0, 0), (0, 0), (0, 0)):
            raise RuntimeError("the window painted nothing (it may be protected or still starting)")
        dw, dh = self.display_size()
        scale = max(1.0, max(img.size) / max_dimension)
        if scale > 1.0:
            img = img.resize((round(img.width / scale), round(img.height / scale)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
        n = u.GetWindowTextLengthW(hwnd)
        tbuf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, tbuf, n + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = ""
        try:
            import psutil
            pname = psutil.Process(pid.value).name()
        except Exception:
            pass
        return Screenshot(image_b64=base64.b64encode(out.getvalue()).decode(), width=img.width, height=img.height,
                          display_width=dw, display_height=dh, scale=w / img.width, origin_x=r.left, origin_y=r.top,
                          captured_at=time.time(), active_window=WindowInfo(title=tbuf.value, pid=pid.value, process=pname))

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
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)         # only a minimised window is restored: a maximised one stays maximised
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
        pol = self.polite

        def before_input() -> bool:
            """Only reached when a control cannot be driven by name and the real mouse or keyboard is needed."""
            if pol is None or not pol.enabled:
                return True
            if not pol.wait_until_idle(getattr(pol, "current_task", "")):
                return False
            pol.remember(getattr(pol, "current_task", ""), self.capture_user_context)
            return True

        def after_input() -> None:
            if pol is not None:
                pol.mark_own_input()
        return uia.act(win, sel, action, text, before_input=before_input, after_input=after_input)

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
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)         # SW_RESTORE, only for a minimised window (a maximised one stays maximised)
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

    def web(self):
        from .cdp import CdpWeb
        if getattr(self, "_web", None) is None:
            self._web = CdpWeb()
        return self._web

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
