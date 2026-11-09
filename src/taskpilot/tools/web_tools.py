"""web.* tools: read and operate a web page through the browser's own debugging interface (exact address, real links,
real search box) instead of reading it through Windows UI Automation. Native apps keep using the ui.* tools.

The same site rules apply as for browser.open_url: private or internal addresses, sites on the never-operate list, and
(in privacy mode) sites the user has not approved. A page that redirects somewhere else is checked again."""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from ..policy import Decision, Policy, base_domain
from .registry import NeedsAccess, Tool, ToolContext, ToolRegistry


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenArgs(_P):
    url: str = Field(min_length=8, max_length=500)


class LinksArgs(_P):
    min_chars: int = Field(20, ge=0, le=200)         # names at least this long are content (titles, headlines), not menus
    max_links: int = Field(25, ge=1, le=60)
    skip: int = Field(0, ge=0, le=500)


class ClickArgs(_P):
    text: str = Field(min_length=2, max_length=160)   # the visible text (or part of it) of the link or button


class SearchArgs(_P):
    query: str = Field(min_length=1, max_length=200)


class ReadArgs(_P):
    max_chars: int = Field(3000, ge=200, le=8000)


class NoArgs(_P):
    pass


def _bridge(ctx: ToolContext):
    web = getattr(ctx.backend, "web", None)
    web = web() if callable(web) else web
    if web is None:
        raise RuntimeError("this computer has no browser bridge")
    return web


def _gate_page(policy: Policy, address: str) -> None:
    """The page the browser is actually on must be allowed (it may have redirected)."""
    u = urlparse(address)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        raise RuntimeError("the browser is not on a web page")
    if policy.host_denied(host):
        raise RuntimeError(f"Deskhand never operates sign-in or payment pages ({host})")
    if policy.require_site_approval and not policy.site_allowed(host):
        raise NeedsAccess(f"you have not approved {base_domain(host)} yet", {"kind": "site", "target": base_domain(host)})


def _check_open(a: OpenArgs, p: Policy) -> Decision:
    from .builtin import UrlArgs, _check_url
    return _check_url(UrlArgs(url=a.url), p)


async def _open(ctx: ToolContext, a: OpenArgs) -> dict:
    r = await asyncio.to_thread(_bridge(ctx).open, a.url)
    _gate_page(ctx.policy, r["address"])
    return r


async def _guard(ctx: ToolContext) -> None:
    st = await asyncio.to_thread(_bridge(ctx).state)
    _gate_page(ctx.policy, st["address"])


async def _links(ctx: ToolContext, a: LinksArgs) -> dict:
    await _guard(ctx)
    r = await asyncio.to_thread(_bridge(ctx).links, a.min_chars, a.max_links, a.skip)
    _gate_page(ctx.policy, r["address"])
    lines = [f"{a.skip + i + 1}. {l['text']}  ->  {l['href'][:110]}" for i, l in enumerate(r["links"])]
    more = r["total"] - a.skip - len(r["links"])
    if more > 0:
        lines.append(f"... {more} more (pass skip={a.skip + len(r['links'])})")
    return {"address": r["address"], "title": r["title"], "count": r["total"], "shown": len(r["links"]), "tree": "\n".join(lines)}


async def _click(ctx: ToolContext, a: ClickArgs) -> dict:
    await _guard(ctx)
    r = await asyncio.to_thread(_bridge(ctx).click, a.text)
    _gate_page(ctx.policy, r["address"])
    return r


async def _search(ctx: ToolContext, a: SearchArgs) -> dict:
    await _guard(ctx)
    r = await asyncio.to_thread(_bridge(ctx).search, a.query)
    _gate_page(ctx.policy, r["address"])
    return r


async def _url(ctx: ToolContext, a: NoArgs) -> dict:
    st = await asyncio.to_thread(_bridge(ctx).state)
    _gate_page(ctx.policy, st["address"])
    return st


async def _read(ctx: ToolContext, a: ReadArgs) -> dict:
    await _guard(ctx)
    return await asyncio.to_thread(_bridge(ctx).text, a.max_chars)


def register_web_tools(r: ToolRegistry) -> None:
    R = r.register
    R(Tool("web.open", "Open a web page in Deskhand's browser and wait until it has loaded. Returns the real address and title.",
           OpenArgs, _open, _check_open,
           lambda a, d: ((True, f"loaded {d.get('address', '')[:90]} titled '{d.get('title', '')[:60]}'") if d.get("loaded")
                         else (False, "the page did not finish loading")),
           idempotent=True, needs_screen=True, example={"url": "https://www.youtube.com/@NASA/videos"}))
    R(Tool("web.links", "List the links on the current web page in page order (top to bottom), as 'text -> address'. "
           "min_chars=20 keeps content (video titles, headlines, results) and drops menus.", LinksArgs, _links, None,
           lambda a, d: (True, f"{d.get('shown')} of {d.get('count')} links on {d.get('address', '')[:80]}"),
           readonly=True, idempotent=True, example={"min_chars": 20}))
    R(Tool("web.click", "Click the link or button on the current web page whose visible text matches. Returns the address "
           "the browser ends on.", ClickArgs, _click, None,
           lambda a, d: (True, f"clicked '{d.get('clicked', '')[:60]}'; browser is on {d.get('address', '')[:90]}"),
           needs_screen=True, example={"text": "Watch now"}))
    R(Tool("web.search", "Type a query into the current page's search box and press Enter (works on YouTube, shops, news "
           "sites). Returns the results page address.", SearchArgs, _search, None,
           lambda a, d: (True, f"searched '{d.get('searched', '')[:50]}'; browser is on {d.get('address', '')[:90]}"),
           needs_screen=True, example={"query": "NetworkChuck paperclip"}))
    R(Tool("web.url", "The real address and title of the page the browser is on.", NoArgs, _url, None,
           lambda a, d: (bool(d.get("address")), f"address: {d.get('address', '')[:120]}"), readonly=True, idempotent=True))
    R(Tool("web.read", "The visible text of the current web page.", ReadArgs, _read, None,
           lambda a, d: (True, f"read {len(d.get('text', ''))} chars"), readonly=True, idempotent=True))
