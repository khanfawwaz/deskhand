"""Is a claim supported by what Pilot actually observed?

A model's "done" came with free-text evidence, and nothing checked it: it once claimed success on the wrong
video. This module scores how much of a claim (the evidence, or the answer in the final message) can be found in
the text Pilot really read (page text, window titles, tool results). It is deliberately simple and cheap: distinctive
words and numbers must be present, and anything in double quotes must appear verbatim.

It returns None when there is too little observed text or too little in the claim to judge: vision-only tasks
(no text observed) are never blocked by it, they are simply not graded.
"""
from __future__ import annotations

import re

STOP = frozenset("""that this with from have been were they them then than there their what when where which while will
would your about after also into over some such only other more most very just like make made does done said each many much
both even still here those these using used use page task user text read looked found shows showing shown opened clicked
completed finished successfully result results""".split())

_TOKEN = re.compile(r"[\w₹$%]+(?:[.,\-][\w₹$%]+)*", re.UNICODE)   # keeps 1,200 / robu.in / 6-7v whole
_QUOTED = re.compile(r"[\"“”]([^\"“”]{4,120})[\"“”]")


def distinctive(text: str) -> set[str]:
    """The SPECIFIC details of a claim: numbers/prices, names with capitals, domains, ids, file names. Ordinary words
    (typed, absent, recommends...) are paraphrase-prone and carry no fabricated facts, so they are not judged."""
    out = set()
    for m in _TOKEN.finditer(text):
        raw = m.group(0).strip(".,-")
        t = raw.lower()
        if not t or t in STOP:
            continue
        before = text[:m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:\n"
        specific = (
            (any(c.isdigit() for c in t) and len(t) >= 2)
            or any(c in raw[1:-1] for c in "._@/-:")
            or (raw[:1].isupper() and len(raw) >= 4 and not sentence_start)
        )
        if specific:
            out.add(t)
    return out


def _squash(s: str) -> str:
    return " ".join(s.lower().split())


def grounding_score(claim: str, observed: list[str], min_corpus: int = 80) -> float | None:
    """0..1 = share of the claim's distinctive terms found in the observed text; None = cannot judge."""
    corpus = _squash("\n".join(observed))
    if len(corpus) < min_corpus or not claim.strip():
        return None
    quotes = _QUOTED.findall(claim)
    for q in quotes:
        if _squash(q) not in corpus:
            return 0.0                                        # a quoted fact that never appeared on screen
    terms = distinctive(claim)
    if len(terms) < 2:
        return 1.0 if quotes else None
    return sum(1 for t in terms if t in corpus) / len(terms)
