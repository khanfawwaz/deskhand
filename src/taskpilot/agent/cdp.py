"""Browser bridge: drive Chrome through its DevTools Protocol (CDP) instead of reading it through Windows UI Automation.

The browser itself tells us the exact address, when a page has loaded, and what links and search boxes the page really
has, in document order. Chrome refuses a debugging port on its default profile folder, so Deskhand keeps its own
browser profile (sign in to Google there once) and starts it with the port bound to this computer only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.environ.get("DESKHAND_CDP_PORT", "9222"))
_BASE = f"http://127.0.0.1:{PORT}"


class BridgeError(RuntimeError):
    pass


def profile_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(os.environ.get("DESKHAND_CHROME_PROFILE", str(Path(root) / "Deskhand" / "chrome-profile")))


def chrome_path() -> str | None:
    for p in (os.environ.get("DESKHAND_CHROME"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
              shutil.which("google-chrome") or "", shutil.which("chromium") or ""):
        if p and Path(p).is_file():
            return p
    return None


def _get(path: str, timeout: float = 3.0, method: str = "GET"):
    req = urllib.request.Request(_BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def is_up() -> bool:
    try:
        return "webSocketDebuggerUrl" in _get("/json/version", 1.5)
    except Exception:
        return False


def ensure_browser(url: str = "about:blank", wait_s: float = 15.0) -> None:
    """Make sure the Deskhand browser (with its debugging port) is running; start it if not."""
    if is_up():
        return
    exe = chrome_path()
    if not exe:
        raise BridgeError("Chrome was not found on this computer")
    profile_dir().mkdir(parents=True, exist_ok=True)
    kw: dict = {}
    if os.name == "nt":                                                # start minimised and WITHOUT taking focus
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7                                             # SW_SHOWMINNOACTIVE
        kw["startupinfo"] = si
    subprocess.Popen([exe, f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1",
                      f"--user-data-dir={profile_dir()}", "--profile-directory=Default", "--no-first-run",
                      "--no-default-browser-check", "--disable-backgrounding-occluded-windows",
                      "--disable-renderer-backgrounding", "--disable-background-timer-throttling", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if is_up():
            return
        time.sleep(0.4)
    raise BridgeError("the Deskhand browser did not start (is another Chrome using the same profile?)")


def _pages() -> list[dict]:
    """Open tabs, the one in front first (several tabs can be open: sign-in pages, the user's own)."""
    tabs = [t for t in _get("/json/list") if t.get("type") == "page" and not str(t.get("url", "")).startswith("devtools://")]
    front = []
    for t in tabs:
        try:
            pg = Page(t)
            try:
                if pg.js("document.visibilityState", 4.0) == "visible":
                    front.append(t)
            finally:
                pg.close()
        except Exception:
            continue
    return front + [t for t in tabs if t not in front]


class Page:
    """One browser tab, spoken to over its websocket."""

    def __init__(self, target: dict):
        from websockets.sync.client import connect
        self.target = target
        self.ws = connect(target["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024, open_timeout=5)
        self._n = 0

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        self._n += 1
        mid = self._n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                msg = json.loads(self.ws.recv(timeout=max(0.1, end - time.monotonic())))
            except TimeoutError:
                break
            if msg.get("id") == mid:
                if "error" in msg:
                    raise BridgeError(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
        raise BridgeError(f"{method}: no answer from the browser")

    def js(self, expression: str, timeout: float = 20.0):
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True}, timeout)
        if "exceptionDetails" in r:
            raise BridgeError("page script failed: " + str(r["exceptionDetails"].get("text", ""))[:120])
        return r.get("result", {}).get("value")

    # ---- what the page is
    def url(self) -> str:
        return str(self.js("location.href") or "")

    def title(self) -> str:
        return str(self.js("document.title") or "")

    def ready(self) -> bool:
        return self.js("document.readyState") == "complete"

    def wait_loaded(self, want_url: str = "", timeout: float = 20.0) -> bool:
        """True when the document is complete (and, if given, the address is the one asked for)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if self.ready() and (not want_url or same_page(self.url(), want_url)):
                    time.sleep(0.8)                      # single-page sites keep filling in after 'complete'
                    return True
            except BridgeError:
                pass
            time.sleep(0.4)
        return False

    def go(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})


def same_page(shown: str, wanted: str) -> bool:
    a, b = urlparse(shown), urlparse(wanted)
    host = lambda u: (u.hostname or "").removeprefix("www.")
    pa, pb = a.path.rstrip("/").lower(), b.path.rstrip("/").lower()
    return host(a) == host(b) and (pa == pb or not pb) and (not b.query or a.query.split("&")[0] == b.query.split("&")[0])


class ExtPage(Page):
    """A tab of the user's own Chrome, spoken to through the Deskhand extension (same commands as a debugging port)."""

    def __init__(self, bridge, tab_id: int):
        self.bridge, self.tab_id = bridge, tab_id

    def close(self) -> None:
        pass

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        try:
            return self.bridge.request({"cmd": "cdp", "tabId": self.tab_id, "method": method, "params": params or {}}, timeout) or {}
        except RuntimeError as e:
            raise BridgeError(f"{method}: {e}") from None


_OWNED: dict[str, int] = {}               # the tab Deskhand opened (so it does not take over the user's own tabs)


def _ext():
    """The connected extension bridge, or None."""
    from .extbridge import get_bridge
    b = get_bridge()
    if b.connected:
        return b
    first = not _OWNED.get("waited")                     # the extension retries every few seconds: allow one full cycle
    _OWNED["waited"] = 1
    return b if b.wait_connected(7.0 if first else 1.0) else None


def _ext_current(b) -> ExtPage:
    tabs = b.request({"cmd": "tabs"}) or []
    if _OWNED.get("tab") in {t["id"] for t in tabs}:
        return ExtPage(b, _OWNED["tab"])
    # Deskhand only ever works in the window it opened itself, never in a tab the person is using
    raise BridgeError("no page is open in Deskhand's own browser window yet: open one with web.open first")


def _ext_open(b, url: str) -> ExtPage:
    tab_id = b.request({"cmd": "openAgent", "url": url})["id"]       # Deskhand's own window, opened or reused, without focus
    _OWNED["tab"] = tab_id
    return ExtPage(b, tab_id)


_DBG: dict[str, str] = {}                  # the debug browser: the id of the tab Deskhand opened (never the person's tabs)


def _own_page() -> "Page | None":
    tid = _DBG.get("target")
    if not tid:
        return None
    for t in _get("/json/list"):
        if t.get("id") == tid and t.get("type") == "page":
            return Page(t)
    _DBG.pop("target", None)
    return None


def _new_background_window() -> "Page":
    """A window of its own, opened behind everything else: Chrome is told not to activate it."""
    ver = _get("/json/version")
    browser = Page({"webSocketDebuggerUrl": ver["webSocketDebuggerUrl"]})
    try:
        tid = browser.call("Target.createTarget", {"url": "about:blank", "newWindow": True, "background": True})["targetId"]
    finally:
        browser.close()
    _DBG["target"] = tid
    for _ in range(20):
        p = _own_page()
        if p is not None:
            return p
        time.sleep(0.1)
    raise BridgeError("could not open Deskhand's browser window")


def open_page(url: str) -> Page:
    """Open `url` in the Deskhand browser (reusing its current tab) and return that tab."""
    b = _ext()
    if b is not None:
        return _ext_open(b, url)
    ensure_browser()
    p = _own_page() or _new_background_window()
    try:
        p.call("Page.enable")
        p.call("Emulation.setFocusEmulationEnabled", {"enabled": True})   # works while covered by other windows; never brought forward
    except BridgeError:
        pass
    p.go(url)
    return p


def current_page() -> Page:
    b = _ext()
    if b is not None:
        return _ext_current(b)
    ensure_browser()
    p = _own_page()
    if p is None:
        raise BridgeError("no page is open in Deskhand's own browser window yet: open one with web.open first")
    return p


# ---------------------------------------------------------------- page reading and acting (all run inside the page)
_LINKS_JS = r"""
(() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    if (!vis(a)) continue;
    let t = (a.getAttribute('aria-label') || a.innerText || a.title || '').replace(/\s+/g, ' ').trim();
    if (!t) { const i = a.querySelector('[title],[aria-label]'); if (i) t = (i.getAttribute('aria-label') || i.title || '').trim(); }
    if (t.length < %(min)d) continue;
    const key = a.href + '|' + t; if (seen.has(key)) continue; seen.add(key);
    const r = a.getBoundingClientRect();
    out.push({text: t.slice(0, 160), href: a.href, y: Math.round(r.top + scrollY)});
  }
  out.sort((p, q) => p.y - q.y);
  return out;
})()
"""

_FIND_JS = r"""
((needle) => {
  const n = needle.toLowerCase().replace(/\s+/g, ' ').trim();
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = e => (e.getAttribute('aria-label') || e.innerText || e.value || e.title || '').replace(/\s+/g, ' ').trim();
  const cands = [...document.querySelectorAll('a[href],button,[role=button],[role=link],[role=tab],input[type=submit],summary')]
      .filter(vis).map(e => ({e, t: label(e)})).filter(x => x.t);
  const inView = e => { const r = e.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };
  const inDialog = e => !!e.closest('[role=dialog],[aria-modal=true],dialog');
  const rank = x => (inDialog(x.e) ? 0 : 2) + (inView(x.e) ? 0 : 1);
  const best = pred => cands.filter(pred).sort((a, b) => rank(a) - rank(b))[0];
  let pick = best(x => x.t.toLowerCase() === n) || best(x => x.t.toLowerCase().startsWith(n))
          || best(x => x.t.toLowerCase().includes(n));
  if (!pick) return null;
  pick.e.scrollIntoView({block: 'center', inline: 'center'});
  const r = pick.e.getBoundingClientRect();
  return {text: pick.t.slice(0, 160), href: pick.e.href || '', x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
})(%(needle)s)
"""

_SEARCH_BOX_JS = r"""
(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 4 && r.height > 4; };
  const sel = 'input[type=search],input[name=search_query],input[name=q],input[role=combobox],input[aria-label*=earch i],'
            + 'input[placeholder*=earch i],[role=searchbox]';
  const e = [...document.querySelectorAll(sel)].find(vis);
  if (!e) return false;
  e.focus(); e.select && e.select();
  return true;
})()
"""


def list_links(p: Page, min_chars: int = 20, limit: int = 30, skip: int = 0) -> list[dict]:
    links = p.js(_LINKS_JS % {"min": int(min_chars)}) or []
    return links[skip:skip + limit]


def click_text(p: Page, text: str) -> dict:
    found = p.js(_FIND_JS % {"needle": json.dumps(text)})
    if not found:
        raise BridgeError(f"nothing clickable on the page matches {text!r}")
    for kind in ("mousePressed", "mouseReleased"):
        p.call("Input.dispatchMouseEvent", {"type": kind, "x": found["x"], "y": found["y"], "button": "left", "clickCount": 1})
    return {"clicked": found["text"], "href": found.get("href", "")}


def search_page(p: Page, query: str) -> dict:
    if not p.js(_SEARCH_BOX_JS):
        raise BridgeError("this page has no search box")
    p.call("Input.insertText", {"text": query})
    for kind in ("keyDown", "keyUp"):
        p.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                                          "text": "\r" if kind == "keyDown" else ""})
    return {"searched": query}


def page_text(p: Page, limit: int = 3000) -> str:
    return str(p.js("document.body ? document.body.innerText : ''") or "")[:limit]


_AD_HOSTS = ("googleadservices.com", "doubleclick.net", "googlesyndication.com", "adservice.google.")


def is_ad(href: str) -> bool:
    h = (urlparse(href).hostname or "").lower()
    return any(a in h for a in _AD_HOSTS)


class CdpWeb:
    """What the web.* tools talk to. One object per agent; each call attaches to the browser's current tab."""

    def open(self, url: str, wait_s: float = 20.0) -> dict:
        p = open_page(url)
        try:
            loaded = p.wait_loaded(url, wait_s)
            return {"address": p.url(), "title": p.title(), "loaded": loaded}
        finally:
            p.close()

    def state(self, launch: bool = True) -> dict:
        if not launch and _ext() is None and not is_up():
            raise BridgeError("the Deskhand browser is not running")
        p = current_page()
        try:
            return {"address": p.url(), "title": p.title()}
        finally:
            p.close()

    def links(self, min_chars: int, limit: int, skip: int) -> dict:
        p = current_page()
        try:
            p.wait_loaded(timeout=8)
            allv = [l for l in list_links(p, min_chars, 400, 0) if not is_ad(l["href"])]
            return {"address": p.url(), "title": p.title(), "total": len(allv), "links": allv[skip:skip + limit]}
        finally:
            p.close()

    def click(self, text: str) -> dict:
        p = current_page()
        try:
            before = p.url()
            r = click_text(p, text)
            end = time.monotonic() + 6.0                       # buttons (Play...) navigate a moment later, not at once
            while time.monotonic() < end and p.url() == before:
                time.sleep(0.5)
            time.sleep(0.5)
            p.wait_loaded(timeout=15)
            return {**r, "address": p.url(), "title": p.title(), "moved": p.url() != before}
        finally:
            p.close()

    def search(self, query: str) -> dict:
        p = current_page()
        try:
            before = p.url()
            r = search_page(p, query)
            time.sleep(1.0)
            p.wait_loaded(timeout=15)
            return {**r, "address": p.url(), "title": p.title(), "moved": p.url() != before}
        finally:
            p.close()

    def text(self, limit: int) -> dict:
        p = current_page()
        try:
            return {"address": p.url(), "title": p.title(), "text": page_text(p, limit)}
        finally:
            p.close()
