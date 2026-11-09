"""Built-in tools. Handlers run on the agent; checks run on server AND agent; verify runs on the server."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import tempfile
import time
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from ..agent.runner import run_command
from ..policy import Decision, Policy, base_domain
from ..titles import title_has
from .registry import NeedsAccess, Tool, ToolContext, ToolRegistry


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- paths

def _in_allowed(path: str, policy: Policy) -> Decision | None:
    """None = fine; otherwise a deny Decision."""
    if not policy.dir_allowed(path):
        return Decision("deny", "path is not inside an authorized directory")
    return None


def _real_allowed(path: str, policy: Policy) -> str:
    """Agent-side defense in depth: resolve symlinks/junctions, then re-check the REAL location."""
    real = os.path.realpath(path)
    if not policy.dir_allowed(real):
        raise PermissionError("resolved path escapes the authorized directories")
    return real


# --------------------------------------------------------------------------- files

class PathArgs(_P):
    path: str = Field(min_length=3, max_length=260)


class ReadArgs(PathArgs):
    max_bytes: int = Field(20000, ge=1, le=200000)


class WriteArgs(PathArgs):
    text: str = Field(max_length=200000)
    overwrite: bool = False


def _check_path(a: PathArgs, p: Policy) -> Decision:
    return _in_allowed(a.path, p) or Decision("allow")


def _check_write(a: WriteArgs, p: Policy) -> Decision:
    if (d := _in_allowed(a.path, p)):
        return d
    if len(a.text.encode("utf-8")) > p.max_file_bytes:
        return Decision("deny", "file content exceeds the configured size limit")
    if a.overwrite and os.path.exists(a.path):          # asking to overwrite a file that is not there is harmless
        return Decision("confirm", "overwriting an existing file")
    return Decision("allow")


async def _files_read(ctx: ToolContext, a: ReadArgs) -> dict:
    def work():
        real = _real_allowed(a.path, ctx.policy)
        if not os.path.isfile(real):
            return {"error": "not a file or does not exist"}
        size = os.path.getsize(real)
        with open(real, "rb") as f:
            raw = f.read(a.max_bytes)
        return {"content": raw.decode("utf-8", "replace"), "bytes_read": len(raw), "size": size,
                "truncated": size > len(raw)}
    return await asyncio.to_thread(work)


async def _files_write(ctx: ToolContext, a: WriteArgs) -> dict:
    def work():
        real = _real_allowed(a.path, ctx.policy)
        if os.path.exists(real) and not a.overwrite:
            return {"error": "file already exists; call again with overwrite=true to replace it"}
        if os.path.isdir(real):
            return {"error": "path is a directory"}
        parent = os.path.dirname(real)
        os.makedirs(parent, exist_ok=True)
        _real_allowed(parent, ctx.policy)
        data = a.text.encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, real)               # atomic: never leaves a half-written target
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        with open(real, "rb") as f:             # read back what is REALLY on disk
            back = f.read()
        return {"path": real, "bytes": len(back), "sha256": _sha(back)}
    return await asyncio.to_thread(work)


def _verify_write(a: WriteArgs, d: dict) -> tuple[bool, str]:
    want = _sha(a.text.encode("utf-8"))
    ok = d.get("sha256") == want
    return ok, (f"file on disk matches ({d.get('bytes')} bytes, sha256 verified)" if ok
                else "content on disk does NOT match what was requested")


async def _files_list(ctx: ToolContext, a: PathArgs) -> dict:
    def work():
        real = _real_allowed(a.path, ctx.policy)
        if not os.path.isdir(real):
            return {"error": "not a directory or does not exist"}
        entries = []
        with os.scandir(real) as it:
            for e in sorted(it, key=lambda e: e.name.lower()):
                entries.append({"name": e.name, "type": "dir" if e.is_dir() else "file",
                                "size": e.stat().st_size if e.is_file() else None})
                if len(entries) >= 200:
                    break
        return {"entries": entries, "truncated": len(entries) >= 200}
    return await asyncio.to_thread(work)


async def _files_exists(ctx: ToolContext, a: PathArgs) -> dict:
    def work():
        real = _real_allowed(a.path, ctx.policy)
        return {"exists": os.path.exists(real),
                "type": "dir" if os.path.isdir(real) else ("file" if os.path.isfile(real) else None)}
    return await asyncio.to_thread(work)


# --------------------------------------------------------------------------- git

class RepoArgs(_P):
    repo: str = Field(min_length=3, max_length=260)


class LogArgs(RepoArgs):
    limit: int = Field(10, ge=1, le=50)


def _check_repo(a: RepoArgs, p: Policy) -> Decision:
    return _in_allowed(a.repo, p) or Decision("allow")


async def _git(ctx: ToolContext, repo: str, args: list[str]) -> dict:
    real = await asyncio.to_thread(_real_allowed, repo, ctx.policy)
    return await run_command(ctx.managed, ctx.task_id, "git", ["-c", "core.quotepath=off", *args],
                             real, 20, 65536)


def _verify_exit0(a, d: dict) -> tuple[bool, str]:
    ok = d.get("exit_code") == 0 and not d.get("timed_out")
    return ok, f"git exit_code={d.get('exit_code')}"


# --------------------------------------------------------------------------- browser

class UrlArgs(_P):
    url: str = Field(min_length=8, max_length=500)


_PRIVATE_NAMES = {"localhost", "metadata.google.internal"}


def _check_url(a: UrlArgs, p: Policy) -> Decision:
    u = urlparse(a.url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return Decision("deny", "only http(s) URLs are allowed")
    if u.username or u.password:
        return Decision("deny", "URLs with embedded credentials are not allowed")
    host = u.hostname.lower()
    if host in _PRIVATE_NAMES or host.endswith((".local", ".internal")):
        return Decision("deny", "local/internal hosts are not allowed")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return Decision("deny", "private or loopback addresses are not allowed")
    except ValueError:
        pass
    if p.allowed_domains and not any(host == d or host.endswith("." + d) for d in p.allowed_domains):
        return Decision("deny", f"domain '{host}' is not on the allowed list")
    if p.host_denied(host):
        return Decision("deny", f"Deskhand never operates sign-in or payment pages ({host})")
    if p.require_site_approval and not p.site_allowed(host):
        return Decision("deny", f"you have not approved {base_domain(host)} yet",
                        access={"kind": "site", "target": base_domain(host)})
    return Decision("allow")


def _same_page(shown: str, wanted: str) -> bool:
    """Does the address bar show the page we asked for? Case-insensitive; a site may add www or keep only the path."""
    a, b = urlparse(shown if "://" in shown else "https://" + shown), urlparse(wanted)
    same_host = base_domain(a.hostname or "") == base_domain(b.hostname or "")
    pa, pb = a.path.rstrip("/").lower(), b.path.rstrip("/").lower()
    same_path = pa == pb or (pb and pa.startswith(pb)) or (pa and pb.startswith(pa) and pa not in ("", "/"))
    if pb in ("", "/"):
        same_path = True
    if b.query and "watch" in pb:                                  # a video address: the video id must match too
        same_path = same_path and a.query.split("&")[0].lower() == b.query.split("&")[0].lower()
    return same_host and bool(same_path)


def _still_loading(title: str) -> bool:
    """While a page loads, a browser's tab is titled with its address ('youtube.com/@x/videos - Google Chrome'), which is
    replaced by the real title moments later. Such a title is not a stable name for the window."""
    t = re.sub(r"\s+[-–—]\s+(google chrome|microsoft.? edge|mozilla firefox|brave|opera).*$", "", str(title or "").strip(), flags=re.I)
    return bool(t) and " " not in t and ("/" in t or t.lower().startswith(("http", "www.")))


async def _wait_for_page(ctx: ToolContext, url: str) -> tuple[str, str]:
    """After opening a URL, wait until the browser really shows THAT page (its address bar matches), so the next step can
    find its window. A window left over from an earlier page does not count. Returns (window title, address) or ('', '')."""
    from ..agent.gate import BROWSERS, window_allowed
    from ..titles import norm
    start = time.monotonic()
    deadline = start + max(0.0, ctx.policy.page_wait_s)
    try:
        w0 = await asyncio.to_thread(ctx.backend.active_window)
        before = norm(w0.title) if w0 else ""
    except Exception:
        before = ""
    seen_at: float | None = None
    last: tuple[str, str] = ("", "")
    while True:
        try:
            w = await asyncio.to_thread(ctx.backend.active_window)
            if w and (w.process or "").lower() in BROWSERS:
                shown = await asyncio.to_thread(ctx.backend.window_url, w.title)
                if shown and _same_page(shown, url):
                    d = await window_allowed(ctx.policy, ctx.backend, w.title, w.process)
                    if d.allowed or not ctx.policy.cloud_safe:         # never leak an unapproved window's title
                        last = (w.title, shown)
                        seen_at = seen_at or time.monotonic()
                        # the address bar updates at once but the tab TITLE lags: wait for it to change from the old
                        # page's title (or, for a page that was already open, give it a short moment)
                        if not _still_loading(w.title) and (norm(w.title) != before or time.monotonic() - seen_at > 2.5):
                            return last
        except Exception:
            return last if last[0] else ("", "")
        if time.monotonic() >= deadline:
            return last if last[0] and seen_at and not _still_loading(last[0]) else ("", "")
        await asyncio.sleep(0.5)


async def _browser_open(ctx: ToolContext, a: UrlArgs) -> dict:
    await asyncio.to_thread(ctx.backend.open_url, a.url)
    title, shown = await _wait_for_page(ctx, a.url)
    return {"opened": True, **({"window": title, "address": shown[:200]} if title else {})}


def _check_private_url(a: UrlArgs, p: Policy) -> Decision:
    if not p.allow_private_windows:
        return Decision("deny", "private browser windows are disabled by policy; use browser.open_url")
    return _check_url(a, p)


async def _browser_open_private(ctx: ToolContext, a: UrlArgs) -> dict:
    d = await asyncio.to_thread(ctx.backend.open_private, a.url)
    return {"opened": True, **d}


# --------------------------------------------------------------------------- windows / clipboard / vscode

class Empty(_P):
    pass


class FocusArgs(_P):
    title: str = Field(min_length=2, max_length=120)


class TextArgs(_P):
    text: str = Field(max_length=5000)


async def _windows_list(ctx: ToolContext, a: Empty) -> dict:
    wins = await asyncio.to_thread(ctx.backend.list_windows)
    if ctx.policy.cloud_safe:                       # privacy mode: other apps' titles never reach the model
        from ..agent.gate import window_allowed
        wins = [w for w in wins if (await window_allowed(ctx.policy, ctx.backend, w["title"], w["process"])).allowed]
    return {"windows": wins[:40]}


async def _windows_focus(ctx: ToolContext, a: FocusArgs) -> dict:
    if ctx.policy.cloud_safe:
        from ..agent.gate import window_allowed
        from ..agent.gate import window_access
        wins = await asyncio.to_thread(ctx.backend.list_windows)
        ok, blocked = False, None
        for w in wins:
            if title_has(w["title"], a.title):
                d = await window_allowed(ctx.policy, ctx.backend, w["title"], w["process"])
                if d.allowed:
                    ok = True
                    break
                if d.access and blocked is None:
                    blocked = (w, d)
        if not ok:
            if blocked:                       # a window like that exists, the user just has not approved it: ask them
                w, d = blocked      # privacy mode: echo only what the model asked for, never the window's own title
                raise NeedsAccess(f"no approved window matches '{a.title}'",
                                  await window_access(ctx.policy, ctx.backend, w["title"], w["process"], d))
            return {"error": f"no approved window matches '{a.title}'"}
    d = await asyncio.to_thread(ctx.backend.focus_window, a.title)
    if not d.get("focused") and not d.get("error"):       # never report a silent non-focus as success
        d["error"] = f"could not bring a window matching '{a.title}' to the front"
    return d


def _verify_focus(a: FocusArgs, d: dict) -> tuple[bool, str]:
    ok = bool(d.get("focused")) and title_has(d.get("active_title", ""), a.title)
    return ok, f"foreground window is '{d.get('active_title')}'"


async def _clip_get(ctx: ToolContext, a: Empty) -> dict:
    return {"text": (await asyncio.to_thread(ctx.backend.get_clipboard))[:5000]}


async def _clip_set(ctx: ToolContext, a: TextArgs) -> dict:
    await asyncio.to_thread(ctx.backend.set_clipboard, a.text)
    back = await asyncio.to_thread(ctx.backend.get_clipboard)
    return {"length": len(back), "sha256": _sha(back.encode("utf-8"))}


def _verify_clip(a: TextArgs, d: dict) -> tuple[bool, str]:
    ok = d.get("sha256") == _sha(a.text.encode("utf-8"))
    return ok, "clipboard read back and matches" if ok else "clipboard does not match"


_SHELL_META = re.compile(r'[&|<>^%"\';`$]')


def _check_vscode(a: PathArgs, p: Policy) -> Decision:
    if (d := _in_allowed(a.path, p)):
        return d
    if _SHELL_META.search(a.path):
        return Decision("deny", "path contains shell metacharacters")
    return Decision("allow")


async def _vscode_open(ctx: ToolContext, a: PathArgs) -> dict:
    real = await asyncio.to_thread(_real_allowed, a.path, ctx.policy)
    pid = await asyncio.to_thread(ctx.backend.launch_cli, "code", [real])
    return {"pid": pid}


# --------------------------------------------------------------------------- registration

async def _current_url(ctx: ToolContext, a: "Empty") -> dict:
    """The address of the page in the foreground browser window (only for an approved window)."""
    from ..agent.gate import BROWSERS, window_allowed
    w = await asyncio.to_thread(ctx.backend.active_window)
    if not w or (w.process or "").lower() not in BROWSERS:      # focus may have drifted: look for an approved browser window
        w = None
        for c in await asyncio.to_thread(ctx.backend.list_windows):
            if (c.get("process") or "").lower() in BROWSERS:
                d = await window_allowed(ctx.policy, ctx.backend, c["title"], c["process"])
                if d.allowed:
                    from ..protocol import WindowInfo
                    w = WindowInfo(title=c["title"], process=c["process"], pid=c.get("pid", 0))
                    break
        if w is None:
            return {"error": "the foreground window is not a browser"}
    d = await window_allowed(ctx.policy, ctx.backend, w.title, w.process)
    if not d.allowed:
        return {"error": "the foreground browser window is not approved"}
    url = await asyncio.to_thread(ctx.backend.window_url, w.title)
    return {"url": (url or "")[:300], "window": w.title} if url else {"error": "could not read the address bar"}


def register_builtin(r: ToolRegistry) -> None:
    from .ui_tools import register_ui_tools
    register_ui_tools(r)
    R = r.register
    R(Tool("files.read", "Read a text file inside an allowed folder.", ReadArgs, _files_read,
           _check_path, lambda a, d: (True, f"read {d.get('bytes_read')} bytes"),
           readonly=True, idempotent=True, example={"path": "C:\\work\\todo.txt"}))
    R(Tool("files.write", "Create a text file (fails if it exists unless overwrite=true, which needs approval).",
           WriteArgs, _files_write, _check_write, _verify_write,
           example={"path": "C:\\work\\notes.txt", "text": "hello"}))
    R(Tool("files.list", "List a folder inside an allowed folder.", PathArgs, _files_list, _check_path,
           lambda a, d: (True, f"{len(d.get('entries', []))} entries"), readonly=True, idempotent=True,
           example={"path": "C:\\work"}))
    R(Tool("files.exists", "Check whether a path exists.", PathArgs, _files_exists, _check_path,
           lambda a, d: (True, f"exists={d.get('exists')}"), readonly=True, idempotent=True,
           example={"path": "C:\\work\\notes.txt"}))
    R(Tool("git.status", "Show branch and changed files of a repo.", RepoArgs,
           lambda c, a: _git(c, a.repo, ["status", "--short", "--branch"]), _check_repo, _verify_exit0,
           readonly=True, idempotent=True, example={"repo": "C:\\work\\repo"}))
    R(Tool("git.log", "Show recent commits of a repo.", LogArgs,
           lambda c, a: _git(c, a.repo, ["log", "--oneline", "--no-color", "-n", str(a.limit)]),
           _check_repo, _verify_exit0, readonly=True, idempotent=True, example={"repo": "C:\\work\\repo"}))
    R(Tool("git.diff", "Show a summary of uncommitted changes of a repo.", RepoArgs,
           lambda c, a: _git(c, a.repo, ["diff", "--stat", "--no-color"]), _check_repo, _verify_exit0,
           readonly=True, idempotent=True, example={"repo": "C:\\work\\repo"}))
    R(Tool("browser.open_url", "Open an http(s) URL in the default browser.", UrlArgs, _browser_open,
           _check_url, lambda a, d: (bool(d.get("opened")), (f"page is open: window '{d['window']}'" if d.get("window")
                                    else "URL handed to the default browser (load not confirmed)")),
           idempotent=True, needs_screen=True, example={"url": "https://example.com"}))
    R(Tool("browser.current_url", "The address of the page in the foreground browser window (to check you are on the right "
           "page, e.g. a video's /watch address).", Empty, _current_url, None,
           lambda a, d: (bool(d.get("url")), f"address: {d.get('url', '')[:120]}"), readonly=True, idempotent=True))
    R(Tool("browser.open_private", "[disabled unless the policy allows it] Open a URL in a PRIVATE (incognito) "
           "window. Prefer browser.open_url.", UrlArgs, _browser_open_private, _check_private_url,
           lambda a, d: (bool(d.get("opened")), f"opened in a private {d.get('browser', 'browser')} window (load not confirmed)"),
           idempotent=True, needs_screen=True, example={"url": "https://example.com"}))
    R(Tool("windows.list", "List visible windows (title, process).", Empty, _windows_list, None,
           lambda a, d: (True, f"{len(d.get('windows', []))} windows"), readonly=True, idempotent=True))
    R(Tool("windows.focus", "Bring a window to the front by (part of) its title.", FocusArgs,
           _windows_focus, None, _verify_focus, idempotent=True, example={"title": "Notepad"}))
    R(Tool("clipboard.get", "Read the clipboard text.", Empty, _clip_get, None,
           lambda a, d: (True, f"{len(d.get('text', ''))} chars"), readonly=True, idempotent=True))
    R(Tool("clipboard.set", "Put text on the clipboard.", TextArgs, _clip_set, None, _verify_clip,
           idempotent=True, example={"text": "hello"}))
    R(Tool("vscode.open", "Open a file or folder in VS Code.", PathArgs, _vscode_open, _check_vscode,
           lambda a, d: (d.get("pid") is not None, "VS Code CLI accepted the path (window not confirmed)"),
           idempotent=True, needs_screen=True, example={"path": "C:\\work\\repo"}))
