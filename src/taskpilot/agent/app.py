"""The Deskhand app for Windows: one program with its own window, notification-area icon and the logo.

    Deskhand.exe boot.py --root <install folder> taskpilot.agent.app [--minimized] [--show] [--wait]

Why a real window: Windows lists a program under "Apps" in Task Manager (next to Discord, Claude...) only when it has a
window. This one opens a small control panel: whether this computer is online, Open Deskhand, Pause/Resume, Quit.
Closing the window keeps Deskhand running in the notification area (like Discord); Quit, or ending the app in Task
Manager, stops it and the helper it supervises, leaving nothing behind.

It owns the Supervisor that runs the helper (the part that does the clicking and typing). Windows only; the window is
built with plain Win32 calls (ctypes), so it needs no GUI toolkit.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

if os.name == "nt":
    from ctypes import wintypes

WM_CLOSE, WM_DESTROY, WM_COMMAND, WM_TIMER, WM_SETFONT = 0x0010, 0x0002, 0x0111, 0x0113, 0x0030
WM_APP = 0x8000
MSG_SHOW, MSG_TOGGLE, MSG_QUIT, MSG_RESTART = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4
ID_OPEN, ID_TOGGLE, ID_QUIT = 1001, 1002, 1003
CLASS_NAME, MUTEX_NAME = "DeskhandMainWindow", "Deskhand.App.Singleton"
NO_WINDOW = 0x08000000
_keep: list = []                                          # callbacks must outlive the window


# ------------------------------------------------------------------ pure logic (testable anywhere)
def status_text(state: str, paused: bool) -> tuple[str, str]:
    """-> (headline, explanation) for the window."""
    if paused:
        return "Paused", "Deskhand is not controlling this computer. Press Resume to bring it back."
    return {"online": ("Online", "You can control this computer from the Deskhand app."),
            "connecting": ("Connecting...", "Reaching your Deskhand server."),
            "stopped": ("Starting...", "The helper is starting up.")}.get(state, ("Starting...", ""))


def computer_name(home: Path) -> str:
    try:
        return json.loads((home / "status.json").read_text(encoding="utf-8-sig")).get("device") or ""
    except (OSError, ValueError):
        pass
    try:
        return json.loads((home / "agent.json").read_text(encoding="utf-8-sig")).get("device_id", "")
    except (OSError, ValueError):
        return ""


# ------------------------------------------------------------------ Win32 plumbing
def _api():
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    LR = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes, u.DefWindowProcW.restype = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], LR
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u.CreateWindowExW.restype = wintypes.HWND
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.IsDialogMessageW.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.MSG)]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.argtypes, u.SendMessageW.restype = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], LR
    u.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    u.FindWindowW.argtypes, u.FindWindowW.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND
    u.LoadIconW.argtypes, u.LoadIconW.restype = [wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HICON
    u.LoadCursorW.argtypes, u.LoadCursorW.restype = [wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HANDLE
    u.GetSysColorBrush.restype = wintypes.HBRUSH
    u.AdjustWindowRectEx.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    k.GetModuleHandleW.restype = wintypes.HMODULE
    k.CreateMutexW.restype = wintypes.HANDLE
    g.CreateFontW.restype = ctypes.c_void_p
    g.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    return u, k, g, LR


class _WNDCLASS(ctypes.Structure if os.name == "nt" else object):
    pass


def _define_wndclass(LR):
    WNDPROC = ctypes.WINFUNCTYPE(LR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]
    return WNDPROC, WNDCLASSW


def _already_running(k, wait: bool) -> bool:
    """True if another Deskhand app is running (this process then just asks it to show its window and exits)."""
    deadline = time.time() + (25 if wait else 0)
    while True:
        h = k.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.get_last_error() != 183:                    # ERROR_ALREADY_EXISTS
            _keep.append(h)
            return False
        k.CloseHandle(h)
        if time.time() >= deadline:
            return True
        time.sleep(0.5)


class DeskhandApp:
    def __init__(self, home: Path, minimized: bool, show: bool):
        from ..agent.launcher import Supervisor
        from .tray import app_url, badge, read_status
        self.home, self.url, self.badge, self.read_status = home, app_url(home), badge, read_status
        self.u, self.k, self.g, LR = _api()
        self.paused, self.quitting, self.tray, self.last_state = False, False, None, None
        self.sup = Supervisor(home, on_update=lambda: self.u.PostMessageW(self.hwnd, MSG_RESTART, 0, 0))
        self.start_hidden = minimized and not show
        self._build(LR)

    # ---------------------------------------------------------------- window
    def _scale(self) -> float:
        try:
            return ctypes.windll.user32.GetDpiForSystem() / 96.0
        except Exception:
            return 1.0

    def _build(self, LR) -> None:
        u, k, g = self.u, self.k, self.g
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)    # crisp text; the layout below scales itself
        except Exception:
            pass
        self.s = self._scale()
        S = lambda v: int(v * self.s)                          # noqa: E731
        WNDPROC, WNDCLASSW = _define_wndclass(LR)
        hinst = k.GetModuleHandleW(None)

        def proc(hwnd, msg, wp, lp):
            return self._wndproc(hwnd, msg, wp, lp)
        cb = WNDPROC(proc)
        _keep.append(cb)
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = cb, hinst, CLASS_NAME
        wc.hIcon = u.LoadIconW(hinst, ctypes.c_void_p(1))      # the Deskhand logo stamped on the program
        wc.hCursor = u.LoadCursorW(None, ctypes.c_void_p(32512))
        wc.hbrBackground = u.GetSysColorBrush(5)               # COLOR_WINDOW
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.RegisterClassW(ctypes.byref(wc))
        style = 0x00C00000 | 0x00080000 | 0x00020000           # caption, system menu, minimise (no resize/maximise)
        r = wintypes.RECT(0, 0, S(400), S(236))
        u.AdjustWindowRectEx(ctypes.byref(r), style, False, 0)
        self.hwnd = u.CreateWindowExW(0, CLASS_NAME, "Deskhand", style, S(200), S(160), r.right - r.left, r.bottom - r.top,
                                      None, None, hinst, None)
        mk = lambda cls, text, x, y, w, h, cid=0, st=0: u.CreateWindowExW(    # noqa: E731
            0, cls, text, 0x50000000 | st, S(x), S(y), S(w), S(h), self.hwnd, cid, hinst, None)
        font = lambda px, w: g.CreateFontW(-S(px), 0, 0, 0, w, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")   # noqa: E731
        f_big, f_norm, f_small = font(26, 700), font(15, 400), font(13, 400)
        self.title = mk("STATIC", "Deskhand", 24, 14, 340, 36)
        self.head = mk("STATIC", "Starting...", 24, 58, 350, 24)
        self.body = mk("STATIC", "", 24, 84, 350, 40)
        self.detail = mk("STATIC", "", 24, 128, 350, 20)
        self.b_open = mk("BUTTON", "Open Deskhand", 24, 158, 168, 34, ID_OPEN, 0x00010001)    # tab stop + default button
        self.b_toggle = mk("BUTTON", "Pause", 206, 158, 168, 34, ID_TOGGLE, 0x00010000)
        self.b_quit = mk("BUTTON", "Quit Deskhand", 24, 198, 168, 28, ID_QUIT, 0x00010000)
        self.hint = mk("STATIC", "Closing this window keeps\nDeskhand running near the clock.", 206, 196, 190, 34)
        for h, f in ((self.title, f_big), (self.head, f_norm), (self.body, f_norm), (self.detail, f_small),
                     (self.b_open, f_norm), (self.b_toggle, f_norm), (self.b_quit, f_norm), (self.hint, f_small)):
            u.SendMessageW(h, WM_SETFONT, f, 1)
        u.SetTimer(self.hwnd, 1, 1000, None)

    def _refresh(self) -> None:
        state, _ = self.read_status(self.home)
        if not self.sup.running and not self.paused:
            state = "stopped"
        head, body = status_text(state, self.paused)
        u = self.u
        u.SetWindowTextW(self.head, ("● " if state == "online" and not self.paused else "") + head)
        u.SetWindowTextW(self.body, body)
        name, ver = computer_name(self.home), self.sup.version
        u.SetWindowTextW(self.detail, f"{name}   ·   version {ver[:7]}" if name else f"version {ver[:7]}")
        u.SetWindowTextW(self.b_toggle, "Resume" if self.paused else "Pause")
        key = "paused" if self.paused else state
        if self.tray is not None and key != self.last_state:
            try:
                self.tray.icon = self.badge("stopped" if self.paused else state)
                self.tray.title = "Deskhand: " + head
            except Exception:
                pass
        self.last_state = key

    def show(self) -> None:
        self.u.ShowWindow(self.hwnd, 9)                        # SW_RESTORE
        self.u.SetForegroundWindow(self.hwnd)
        self._refresh()

    def toggle(self) -> None:
        if self.paused:
            self.paused = False
            self.sup.start()
        else:
            self.paused = True
            threading.Thread(target=self.sup.stop, daemon=True).start()      # never block the window while it stops
        self._refresh()

    def open_web(self) -> None:
        if self.url:
            webbrowser.open(self.url)

    def quit(self) -> None:
        self.quitting = True
        self.sup.stop()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.u.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)

    def restart_for_update(self) -> None:
        """A new version was installed: start a fresh copy of the app (so the window code updates too), then exit."""
        visible = bool(self.u.IsWindowVisible(self.hwnd))
        self.sup.stop()
        flags = ["--show" if visible else "--minimized", "--wait"]
        subprocess.Popen([sys.executable, str(self.home / "boot.py"), "--root", str(self.home), "taskpilot.agent.app", *flags],
                         cwd=str(self.home), creationflags=NO_WINDOW | 0x00000008)   # detached: not tied to this process
        self.quit()

    def _wndproc(self, hwnd, msg, wp, lp):
        u = self.u
        try:
            if msg == WM_COMMAND:
                cid = wp & 0xFFFF
                if cid == ID_OPEN:
                    self.open_web()
                elif cid == ID_TOGGLE:
                    self.toggle()
                elif cid == ID_QUIT:
                    self.quit()
                return 0
            if msg == 0x0138:                                  # WM_CTLCOLORSTATIC: text boxes blend into the white window
                self.g.SetBkMode(wp, 1)                       # TRANSPARENT
                return u.GetSysColorBrush(5)
            if msg == WM_TIMER:
                self._refresh()
                return 0
            if msg == MSG_SHOW:
                self.show()
                return 0
            if msg == MSG_TOGGLE:
                self.toggle()
                return 0
            if msg == MSG_QUIT:
                self.quit()
                return 0
            if msg == MSG_RESTART:
                self.restart_for_update()
                return 0
            if msg == WM_CLOSE:
                if self.quitting:
                    return u.DefWindowProcW(hwnd, msg, wp, lp)
                u.ShowWindow(hwnd, 0)                         # closing the window keeps Deskhand running in the tray
                return 0
            if msg == WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return u.DefWindowProcW(hwnd, msg, wp, lp)

    # ---------------------------------------------------------------- notification-area icon
    def start_tray(self) -> None:
        try:
            import pystray
        except Exception:
            return
        post = lambda m: (lambda icon, item: self.u.PostMessageW(self.hwnd, m, 0, 0))     # noqa: E731
        menu = pystray.Menu(
            pystray.MenuItem("Open Deskhand window", post(MSG_SHOW), default=True),
            pystray.MenuItem("Open the Deskhand app in the browser", lambda icon, item: self.open_web()),
            pystray.MenuItem(lambda item: "Resume" if self.paused else "Pause", post(MSG_TOGGLE)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Deskhand", post(MSG_QUIT)),
        )
        self.tray = pystray.Icon("Deskhand", self.badge("stopped"), "Deskhand", menu)
        self.tray.run_detached()

    def run(self) -> int:
        u = self.u
        self.start_tray()
        self.sup.start()
        self._refresh()
        if not self.start_hidden:
            self.show()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if not u.IsDialogMessageW(self.hwnd, ctypes.byref(msg)):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if os.name != "nt":
        raise SystemExit("The Deskhand app runs on Windows only.")
    home = Path(os.environ.get("PILOT_HOME") or Path.cwd())
    u, k, _, _ = _api()
    if _already_running(k, wait="--wait" in argv):
        hwnd = u.FindWindowW(CLASS_NAME, None)
        if hwnd:
            u.PostMessageW(hwnd, MSG_SHOW, 0, 0)               # a second launch just brings the window forward
        return 0
    return DeskhandApp(home, minimized="--minimized" in argv, show="--show" in argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
