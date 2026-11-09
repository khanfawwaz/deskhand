"""Window titles drift while a page is open: YouTube, Gmail and others prefix a live notification counter, so the same
window is "(349) SSundeePlus - YouTube" and a moment later "(350) SSundeePlus - YouTube". Matching must ignore that."""
from __future__ import annotations

import re

_COUNTER = re.compile(r"^\s*\(\d+\+?\)\s*")


def norm(title: object) -> str:
    """Lower-case, single-spaced, without a leading '(12) ' notification counter."""
    return " ".join(_COUNTER.sub("", str(title or "")).lower().split())


def title_has(title: object, query: object) -> bool:
    """Does `title` contain `query`, ignoring case, spacing and notification counters on either side?"""
    q = norm(query)
    return bool(q) and q in norm(title)
