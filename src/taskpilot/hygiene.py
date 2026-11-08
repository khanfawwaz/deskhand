"""Anything learned from a screen is DATA from an untrusted source. Before it is stored (and later shown to the model as a
hint) it is made one short line and dropped if it looks like an instruction, a link or a secret."""
from __future__ import annotations

import re

_BAD = re.compile(r"[^\w .,'&()/:+#@%\-]")
_QUOTED = re.compile(r"[^\w .,'&()/:+#@%\-\"]")
_SUSPICIOUS = re.compile(r"ignore|disregard|instruction|system prompt|assistant|jailbreak|password|passcode|credential|"
                         r"api.?key|secret|token|https?:|www\.", re.I)


def clean(s: object, n: int = 60) -> str:
    """One line, harmless characters only, length-limited."""
    return " ".join(_BAD.sub(" ", str(s or "")).split())[:n].rstrip()


def line(s: object, n: int = 90) -> str:
    """Like clean() but keeps double quotes (for rendered step text such as: click "Play")."""
    return " ".join(_QUOTED.sub(" ", str(s or "")).split())[:n].rstrip()


def safe(*parts: str) -> bool:
    return not any(_SUSPICIOUS.search(p or "") for p in parts)
