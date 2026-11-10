"""Saved preferences: things the person tells Deskhand to remember ("when I say play calming music, play lofi music, set
it as a preference"). A message that is about remembering something is NOT a task: it is saved, confirmed, and nothing is
run. Later tasks get the saved preferences in front of the model so they are followed.

This is deliberately a small, predictable rule set (no model call): it must never start something by surprise, and it
must never swallow a real instruction, so it only fires on clear "remember / preference / from now on / when I say" wording.
"""
from __future__ import annotations

import re

MAX_TEXT = 300
MAX_PER_WORKSPACE = 50

_PRON = r"(?:it|this|that|these|them)"
_SAVE_AS = (rf"\b(?:set|save|store|keep|make|add|put)\s+{_PRON}\s+(?:as|in|to|under)\s+"
            r"(?:a\s+|my\s+|the\s+)?(?:preferences?|defaults?|memory)\b")
_MARKERS = [
    _SAVE_AS,
    r"\b(?:as|for)\s+(?:a\s+|my\s+)?preferences?\b",
    r"\bremember\s+(?:that|this|it|my|i|me|these)\b",
    r"\bfrom now on\b",
    r"\bi\s+(?:always\s+)?prefer\b",
    r"\bmy\s+preference\s+is\b",
    r"\bwhen(?:ever)?\s+i\s+(?:say|ask|tell|type)\b",
    r"\bevery\s+time\s+i\s+(?:say|ask|tell)\b",
    r"\bnext\s+time\b",
    r"\bby default\b",
    r"\bmake\s+\S+(?:\s+\S+){0,3}\s+(?:my|the)\s+default\b",
]
_MARK = re.compile("|".join(f"(?:{m})" for m in _MARKERS), re.I)

# words that mean the person is asking ABOUT preferences, not setting one
_QUESTION_START = re.compile(r"^\s*(?:what|which|how|show|list|tell me|do you|can you tell|are there)\b", re.I)

_STRIP = [
    rf"\b(?:and\s+)?{_SAVE_AS[2:]}",                      # "(and) set it as preference"
    r"\b(?:as|for)\s+(?:a\s+|my\s+)?preferences?\b",
    r"\bremember\s+(?:that|this|it)\b\s*[:,]?",
    r"\bfrom now on\b\s*[:,]?",
]
_STRIP_RE = [re.compile(p, re.I) for p in _STRIP]
_LEAD = re.compile(r"^\s*(?:ok(?:ay)?|so|hey|please|also|and|hmm|um)\b[\s,.:;-]*", re.I)

_VERBS = (r"play|open|use|search|go to|show|start|turn|do|run|launch|put on|find|watch|listen|set|send|write|"
          r"always|just")
_WHEN = re.compile(
    r"^\s*when(?:ever)?\s+i\s+(?:say|ask|tell(?:\s+you)?|type)\s+(?P<trigger>.+?)[,;:]?\s+(?:then\s+|you\s+(?:should|can)\s+|to\s+)?"
    rf"(?P<action>(?:{_VERBS})\b.*)$", re.I | re.S)


def is_preference(goal: str) -> bool:
    g = (goal or "").strip()
    if len(g) < 6 or g.endswith("?") or _QUESTION_START.match(g):
        return False
    return bool(_MARK.search(g))


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" \t,.;:-")
    return s


def parse_preference(goal: str) -> dict | None:
    """None when the message is an ordinary task. Otherwise {"text": the preference as one clear sentence}, with an empty
    text when the person said "remember this" but not what."""
    if not is_preference(goal):
        return None
    t = goal.strip()
    for rx in _STRIP_RE:
        t = rx.sub(" ", t)
    t = _tidy(t)
    for _ in range(3):                                       # "ok so, hey ..." (more than one filler word)
        t2 = _tidy(_LEAD.sub("", t))
        if t2 == t:
            break
        t = t2
    if len(t) < 3:
        return {"text": ""}                                 # "set it as a preference" with nothing to save
    m = _WHEN.match(t)
    if m:
        trig, act = _tidy(m.group("trigger")), _tidy(m.group("action"))
        if trig and act:
            t = f"When you say “{trig}”: {act}"
    t = t[:1].upper() + t[1:]
    return {"text": t[:MAX_TEXT]}


def render_for_model(prefs: list[dict], limit: int = 20, budget: int = 1500) -> str:
    """The saved preferences as a short block for the model's instructions ('' when there are none)."""
    lines, used = [], 0
    for p in prefs[:limit]:
        line = "- " + str(p.get("text", ""))[:MAX_TEXT]
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return ("The person's saved preferences (a guide, not a rulebook: things they told you they like. They often help "
            "when they fit this task; use your judgment, and a preference on its own is not a reason to start anything):\n"
            + "\n".join(lines))
