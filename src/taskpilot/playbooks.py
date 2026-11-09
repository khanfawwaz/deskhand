"""App playbooks: short, trusted tips shown to the model when the goal mentions a known app.

They come from this repo (never from a page, file or screen), so unlike screen text they ARE safe to follow.
They describe where things usually are; the model must still look (ui.snapshot) before acting, because
web UIs change. A playbook never grants permission: every step still passes the policy.
"""
from __future__ import annotations

import re

NORMAL = ("Open pages with browser.open_url (the normal browser; never a private window). ")

PLAYBOOKS: list[dict] = [
    {"name": "YouTube", "keywords": ("youtube", "you tube"), "text": (
        "Use the web tools (they read the real page and its real address). A channel's Videos tab lists uploads NEWEST FIRST: "
        "web.open https://www.youtube.com/@<handle>/videos, then web.links (min_chars=20 shows video titles in page order) "
        "and web.click the first title for 'the latest'. For a video you can only describe (a topic, not 'the latest'), "
        "search beats browsing a channel: web.open https://www.youtube.com/results?search_query=<channel>+<topic>, web.links, "
        "web.click the matching result (skip ads). If unsure of a handle, search for the channel the same way. SUCCESS: "
        "web.url shows an address with /watch (or /shorts/): finish with outcome done and cite the video title. "
        "Without the web tools: browser.open_url (the normal browser; never a private window), then ui.snapshot role=Hyperlink min_chars=25 lists the newest videos; take "
        "a screenshot (capture_screen) if the list looks wrong.")},
    {"name": "News research", "keywords": ("news", "research"), "text": NORMAL + (
        "For today's news use https://news.google.com/search?q=<words>+when:1d (for AI: q=artificial+intelligence+"
        "OR+AI+when:1d). Read the headlines straight from that results page: ui.snapshot with name_chars=200 "
        "(headlines are Hyperlinks, followed by source and age text). Do NOT click into articles. Finish with "
        "outcome done: message = 5-7 bullets 'headline - source - how long ago', evidence = what you read. "
        "Report only what the page showed.")},
    {"name": "ChatGPT", "keywords": ("chatgpt", "chat gpt"), "text": NORMAL + (
        "Open https://chatgpt.com . The prompt box is a large text field (named like 'Ask ChatGPT' or 'Ask anything'): "
        "ui.type the question, then press Enter (or ui.click 'Send prompt'). After you send, the window title "
        "becomes the CHAT's name (no longer 'ChatGPT'): from then on use window='' (the foreground window). The "
        "answer streams in: call ui.read_page (window='') - it WAITS until the answer is complete and returns the page "
        "text. Put the answer (the recommendation, prices, cautions) in your done message; never finish before reading "
        "it. If a login wall, 'verify you are human' check or CAPTCHA "
        "appears, stop with outcome cannot_proceed and say so: never try to bypass it.")},
    {"name": "Claude", "keywords": ("claude", "claude.ai"), "text": NORMAL + (
        "Open https://claude.ai/new . The prompt box is a rich text field (named like 'Write your prompt to Claude' "
        "or 'How can I help you today?'): ui.type the question, then press Enter (or ui.click 'Send message'). After "
        "you send, the window title becomes the CHAT's name (no longer 'Claude'): from then on use window='' (the "
        "foreground window). The answer streams in: call ui.read_page (window='') - it WAITS until the answer is complete "
        "and returns the page text. Put the answer (the recommendation, prices, cautions) in your done message; never "
        "finish before reading it. If a login wall or human-verification check appears, "
        "stop with outcome cannot_proceed: never try to bypass it.")},
    {"name": "Look something up", "keywords": ("openrouter", "look up", "find out", "search for", "google"), "text": (
        "To find a website or how something works, search in the user's browser: browser.open_url "
        "https://www.google.com/search?q=<the+words> (the user is asked to allow google.com the first time), read the "
        "results with ui.read_page, then open the best result with browser.open_url (the user is asked about that site "
        "too). Report what you read; never invent a link.")},
    {"name": "Spotify", "keywords": ("spotify",), "text": (
        "Open it with launch_application app_id spotify (the user is asked to allow it the first time; you will be told "
        "the answer). Its window title is 'Spotify'. Use ui.snapshot to find the Search box and the Play button by "
        "name, ui.type the artist or song, press Enter, then ui.click the first result and Play. SUCCESS: ui.read shows "
        "a track playing or the Pause button is present. If the user declines, do not retry: ask where they want it.")},
    {"name": "Play something", "keywords": ("relaxing", "relax", "chill", "calming", "music", "song", "songs", "playlist",
                                            "something to listen", "play some"),
     "unless": ("youtube", "spotify", "you tube", "apple music", "soundcloud"), "text": (
        "The user did NOT say where to play it, so work through these IN ORDER and stop at the first that works. "
        "(1) YouTube: browser.open_url https://www.youtube.com/results?search_query=<what+they+asked> (the user is asked to "
        "allow YouTube first), then ui.click the first video. (2) If YouTube access is declined or fails: Spotify: "
        "launch_application app_id spotify and play there. (3) If both are declined or neither works: finish with "
        "outcome need_more_information, message 'Where would you like me to play it?', options ['YouTube', 'Spotify', "
        "'Somewhere else']. Never repeat a step the user declined.")},
    {"name": "Gmail", "keywords": ("gmail", "email", "emails", "e-mail", "inbox", "my mail"), "text": (
        "Open https://mail.google.com. Find mail from someone with the search box (named 'Search mail'): ui.type "
        "from:<address> then press Enter, then ui.click the first result. 'Reply' is a button on the open email; "
        "the reply text goes in the field named 'Message Body'. Typing is safe; clicking 'Send' sends for real.")},
    {"name": "WhatsApp Web", "keywords": ("whatsapp", "whats app"), "text": (
        "Open https://web.whatsapp.com (it keeps its login). Find a chat with the search box named 'Search or "
        "start a new chat': ui.type the chat name, then ui.click the matching chat in the list. The message box "
        "is named 'Type a message'; press Enter (or click 'Send') to send, which is irreversible.")},
]


def playbooks_for(goal: str, limit: int = 1800) -> str:
    g = goal.lower()
    hits = [p for p in PLAYBOOKS if any(re.search(rf"\b{re.escape(k)}\b", g) for k in p["keywords"])
            and not any(re.search(rf"\b{re.escape(u)}\b", g) for u in p.get("unless", ()))]
    return "\n".join(f"[{p['name']}] {p['text']}" for p in hits)[:limit]


# What a task must have DONE before "done" is accepted (enforced in code, not left to the model).
#   requires: at least one of these tools must have succeeded in the task
#   min_message_chars: the done message must actually contain the result, not just say one exists
REQUIREMENTS = {
    "ChatGPT": {"requires": ("ui.read_page",), "min_message_chars": 150},
    "Claude": {"requires": ("ui.read_page",), "min_message_chars": 150},
    "News research": {"requires": ("ui.snapshot", "ui.read_page"), "min_message_chars": 150},
}


def final_address_for(goal: str) -> tuple[str, ...]:
    """What the browser's address must contain for this goal to count as done (checked by code, not by the model).
    Playing a YouTube video must END on a video page: '/watch' (or a Short / live stream), not a channel or search list."""
    g = goal.lower()
    if re.search(r"\b(youtube|you tube)\b", g) and re.search(r"\b(play|watch|run)\b", g):
        return ("/watch", "/shorts/", "/live/")
    return ()


def requirements_for(goal: str) -> list[dict]:
    g = goal.lower()
    out = []
    for p in PLAYBOOKS:
        if p["name"] in REQUIREMENTS and any(re.search(rf"\b{re.escape(k)}\b", g) for k in p["keywords"]):
            out.append({"name": p["name"], **REQUIREMENTS[p["name"]]})
    return out
