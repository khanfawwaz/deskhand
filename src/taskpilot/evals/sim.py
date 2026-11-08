"""A small simulated desktop so the harness (and Pilot's behaviour) can be tested offline and repeatably.

Pages are plain data: a title, a host, UI elements (some navigate or run a callback when clicked) and page text.
The selection logic is the REAL one (taskpilot.agent.uia), so selector semantics match Windows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..agent import uia
from ..agent.backend import Backend
from ..protocol import Screenshot, WindowInfo


@dataclass
class Page:
    title: str
    host: str = ""
    process: str = "chrome.exe"
    text: str = ""
    elements: list[dict] = field(default_factory=list)


def el(role: str, name: str, id_: str = "", *, value=None, state=(), goto: str | None = None,
       on_click: Callable[["SimBackend"], None] | None = None) -> dict:
    return {"role": role, "name": name, "id": id_, "value": value, "state": list(state), "enabled": True,
            "goto": goto, "on_click": on_click}


class SimBackend(Backend):
    def __init__(self, pages: dict[str, Page], apps: dict[str, Page] | None = None):
        self.pages = pages                       # url prefix -> page
        self.apps = apps or {}                   # app exe name (lower) -> page opened by launch()
        self.windows: list[tuple[Page, int]] = []
        self.active: Page | None = None
        self.opened: list[str] = []
        self.typed: dict[str, str] = {}
        self.clipboard = ""
        self._pid = 1000

    # ------------------------------------------------------------------ world mechanics
    def _show(self, page: Page) -> Page:
        for p, _ in self.windows:
            if p is page:
                break
        else:
            self._pid += 1
            self.windows.append((page, self._pid))
        self.active = page
        return page

    def navigate(self, url: str) -> None:
        self.opened.append(url)
        key = max((k for k in self.pages if url.lower().startswith(k.lower())), key=len, default=None)   # handles are case-insensitive
        page = self.pages[key] if key else Page(title="This site can't be reached - Google Chrome",
                                                text="ERR_NAME_NOT_RESOLVED")
        # a browser reuses its window: replace the page of the current browser window
        self.windows = [(p, pid) for p, pid in self.windows if p.process != "chrome.exe"]
        self._show(page)

    def _el(self, page: Page) -> list[dict]:
        return page.elements

    def _win(self, page: Page) -> dict:
        pid = next(pid for p, pid in self.windows if p is page)
        return {"title": page.title, "process": page.process, "pid": pid, "handle": pid}

    # ------------------------------------------------------------------ Backend API
    def display_size(self): return (1920, 1080)

    def screenshot(self, max_dimension):
        return Screenshot(image_b64="AAAA", width=960, height=540, display_width=1920, display_height=1080, scale=2.0,
                          captured_at=0, active_window=WindowInfo(title=self.active.title if self.active else "Desktop"))

    def screenshot_window(self, max_dimension): return self.screenshot(max_dimension)

    def active_window(self):
        return WindowInfo(title=self.active.title, process=self.active.process) if self.active else WindowInfo(title="Desktop")

    def list_windows(self):
        return [{"title": p.title, "process": p.process, "pid": pid} for p, pid in self.windows]

    def focus_window(self, title):
        hit = next((p for p, _ in self.windows if title.lower() in p.title.lower()), None)
        if hit:
            self.active = hit
        return {"focused": bool(hit), "active_title": self.active.title if self.active else ""}

    def launch(self, target):
        key = target.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        page = self.apps.get(key)
        if page is None:
            raise FileNotFoundError(target)
        self._show(page)
        return next(pid for p, pid in self.windows if p is page)

    def process_status(self, target, pid, hint=None):
        page = next((p for p, i in self.windows if i == pid), None)
        if page is None and hint and self.apps.get(hint.lower()) is not None:
            page = next((p for p, _ in self.windows if p is self.apps[hint.lower()]), None)
            pid = next((i for p, i in self.windows if p is page), pid)
        return {"running": page is not None, "pid": pid, "window_visible": page is not None,
                "title": page.title if page else ""}

    def open_url(self, url): self.navigate(url)
    def get_clipboard(self): return self.clipboard
    def set_clipboard(self, text): self.clipboard = text
    def click(self, x, y, button, clicks): pass
    def type_text(self, text): pass
    def press(self, keys): pass
    def scroll(self, x, y, clicks): pass
    def launch_cli(self, exe, args): return 1

    def window_host(self, title):
        p = next((p for p, _ in self.windows if title.lower() in p.title.lower()), None)
        return p.host if p and p.host else None

    def open_private(self, url): raise PermissionError("private windows are not available in the simulation")

    # ---- UI automation
    def ui_window(self, title):
        t = (title or (self.active.title if self.active else "")).lower()
        page = next((p for p, _ in self.windows if t in p.title.lower()), None)
        return self._win(page) if page else None

    def _page(self, win) -> Page | None:
        return next((p for p, _ in self.windows if p.title == win["title"]), None)

    @staticmethod
    def _line(e, nth=None):
        parts = [f'{e["role"]} "{e["name"]}"', f"id={e['id']}"] if e["id"] else [f'{e["role"]} "{e["name"]}"']
        if e["value"] is not None:
            parts.append(f'value="{e["value"]}"')
        if nth:
            parts.append(f"#{nth}")
        return " ".join(parts + [f"[{s}]" for s in e["state"]])

    def ui_snapshot(self, win, max_elements, query, name_chars=90):
        page = self._page(win)
        els = [e for e in (page.elements if page else []) if query.lower() in (e["name"] + e["id"]).lower()]
        shown = els[:max_elements]
        return {"tree": "\n".join(self._line(e) for e in shown), "count": len(els), "shown": len(shown),
                "truncated": len(els) > len(shown), "window": {"title": win["title"], "process": win["process"]},
                "hint": ""}

    def ui_act(self, win, sel, action, text):
        page = self._page(win)
        if page is None:
            raise LookupError("window closed")
        cands = uia._matches(page.elements, sel)
        e, err = uia.choose(cands, sel, action, describe=self._line)
        if err:
            return err
        d = self._line(e)
        if action == "read":
            return {"text": e["value"] if e["value"] is not None else e["name"], "element": d,
                    "source": "value" if e["value"] is not None else "name"}
        if action in ("click", "double_click"):
            if e["on_click"]:
                e["on_click"](self)
            if e["goto"]:
                self.navigate(e["goto"])
            return {"method": "invoke", "element": d}
        if action == "type":
            e["value"] = text
            return {"method": "value", "element": d, "readback_available": True, "value_after": text}
        return {"error": f"unknown action '{action}'"}

    def ui_wait(self, win, sel, timeout_s, gone):
        page = self._page(win)
        present = bool(page and uia._matches(page.elements, sel))
        if present != gone:
            return {"met": True, "waited_s": 0.0}
        return {"error": f"timed out after {timeout_s}s waiting for {uia._sel_text(sel)}"}

    def ui_read_page(self, win, min_wait_s, quiet_s, max_wait_s, max_chars):
        page = self._page(win)
        text = page.text if page else ""
        return {"page_text": text[-max_chars:], "chars": len(text), "truncated": len(text) > max_chars,
                "stable": True, "still_generating": False, "waited_s": 0.0}

    def screenshot_region(self, x, y, w, h, max_dimension, upscale=True):
        return self.screenshot(max_dimension)


# ---------------------------------------------------------------------- ready-made worlds
def calculator_page() -> tuple[Page, Callable[[], str]]:
    state = {"acc": None, "op": None, "cur": "0", "fresh": True}
    names = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
    page = Page(title="Calculator", process="ApplicationFrameHost.exe")

    def refresh():
        for e in page.elements:
            if e["id"] == "CalculatorResults":
                e["name"] = f"Display is {state['cur']}"

    def press(n):
        def f(_):
            if n in names:
                d = str(names.index(n))
                state["cur"] = d if state["fresh"] or state["cur"] == "0" else state["cur"] + d
                state["fresh"] = False
            elif n in ("Plus", "Minus"):
                state.update(acc=int(state["cur"]), op=n, fresh=True)
            elif n == "Equals" and state["op"]:
                b = int(state["cur"])
                state["cur"] = str(state["acc"] + b if state["op"] == "Plus" else state["acc"] - b)
                state.update(op=None, fresh=True)
            elif n == "Clear":
                state.update(acc=None, op=None, cur="0", fresh=True)
            refresh()
        return f
    page.elements = [el("Button", n, f"num{i}Button", on_click=press(n)) for i, n in enumerate(names)]
    page.elements += [el("Button", n, f"{n.lower()}Button", on_click=press(n)) for n in ("Plus", "Minus", "Equals", "Clear")]
    page.elements.append(el("Text", "Display is 0", "CalculatorResults"))
    return page, lambda: state["cur"]


def youtube_world(videos: list[tuple[str, str]], channel: str = "BattleBots") -> tuple[SimBackend, str]:
    """videos = [(id, title)] NEWEST FIRST. The channel page lists them in that order. Returns (backend, newest title)."""
    pages: dict[str, Page] = {}
    chan = Page(title=f"{channel} - YouTube - Google Chrome", host="www.youtube.com",
                text=f"{channel} Videos\n" + "\n".join(f"{t} {i+1} days ago" for i, (_, t) in enumerate(videos)))
    for vid, title in videos:
        pages[f"https://www.youtube.com/watch?v={vid}"] = Page(
            title=f"{title} - YouTube - Google Chrome", host="www.youtube.com",
            text=f"{title}\n{channel}\nSubscribe\nComments",
            elements=[el("Button", "Play", "playBtn", state=["playing"])])
        chan.elements.append(el("Hyperlink", title, "", goto=f"https://www.youtube.com/watch?v={vid}"))
    pages["https://www.youtube.com/@" + channel + "/videos"] = chan
    pages["https://www.youtube.com/results"] = Page(
        title=f"{channel} - YouTube - Google Chrome", host="www.youtube.com", text=chan.text,
        elements=[el("Hyperlink", channel, "chan", goto="https://www.youtube.com/@" + channel + "/videos")])
    pages["https://www.youtube.com"] = pages["https://www.youtube.com/results"]
    return SimBackend(pages), videos[0][1]
