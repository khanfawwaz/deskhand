"""Window gate used everywhere the agent decides 'may this window be automated / shown to the model?'.

Title and process rules are checked first (cheap). For browsers, a window whose TITLE says nothing useful (a chat
page is named after the chat, a video page after the video) can still be approved by the website it shows: the
browser's address bar is read and its host compared with `Policy.ui_allowed_hosts`. Deny rules always win.
"""
from __future__ import annotations

import asyncio

from ..policy import Decision, Policy, base_domain

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


async def window_access(policy: Policy, backend, title: str, process: str, decision: Decision) -> dict | None:
    """What the USER should be asked to approve for a blocked window: a website (for a browser tab) or the program."""
    if not decision.access:
        return None
    if (process or "").lower() in BROWSERS:
        try:
            host = await asyncio.to_thread(backend.window_host, title)
        except Exception:
            host = None
        if host and not policy.host_denied(host):
            return {"kind": "site", "target": base_domain(host)}
        return {"kind": "window", "target": title, "process": process}
    return decision.access


async def window_allowed(policy: Policy, backend, title: str, process: str) -> Decision:
    d = policy.ui_window_allowed(title, process)
    if d.allowed or not policy.ui_allowed_hosts:
        return d
    if (process or "").lower() not in BROWSERS or "protected window" in d.reason:
        return d                                              # protected windows are never rescued by a host rule
    try:
        host = await asyncio.to_thread(backend.window_host, title)
    except Exception:
        host = None
    return policy.ui_window_allowed(title, process, host) if host else d
