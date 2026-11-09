"""The task suites. `sim` runs on a simulated desktop (fast, free, repeatable); `live` drives THIS Windows desktop."""
from __future__ import annotations

import re
import urllib.request

from . import verifiers as V
from .sim import calculator_page, youtube_world
from .spec import TaskSpec

NEWEST_BATTLEBOTS = [("v3", "Season finale: Tombstone vs Witch Doctor"), ("v2", "Bracket reveal"), ("v1", "Old fight")]


_LAST_NEWEST_ID = [""]            # the video id behind the last newest-title lookup (titles can be A/B tested; ids cannot)


def _fetch_newest_title(channel_url: str) -> tuple[bool | None, str, str]:
    """Public truth, outside Pilot: the title of the newest upload on a channel's /videos page.
    The first video id on the page is the newest; its title comes from YouTube's public oEmbed endpoint."""
    import json
    hdr = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
    try:
        html = urllib.request.urlopen(urllib.request.Request(channel_url, headers=hdr), timeout=20).read().decode("utf-8", "replace")
        m = re.search(r'"contentId":"([\w-]{11})","contentType":"LOCKUP_CONTENT_TYPE_VIDEO"', html) or             re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)
        if not m:
            return None, "", "public page had no recognisable newest video"
        _LAST_NEWEST_ID[0] = m.group(1)
        o = f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={m.group(1)}"
        return True, json.loads(urllib.request.urlopen(urllib.request.Request(o, headers=hdr), timeout=20).read())["title"], ""
    except Exception as e:
        return None, "", f"could not fetch the public channel page: {type(e).__name__}"


_YT_HDR = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en", "Cookie": "CONSENT=YES+1; SOCS=CAI"}


def _yt_get(url: str) -> str:
    return urllib.request.urlopen(urllib.request.Request(url, headers=_YT_HDR), timeout=20).read().decode("utf-8", "replace")


def _yt_title(vid: str) -> tuple[str, str]:
    import json
    o = json.loads(_yt_get(f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={vid}"))
    return o["author_name"], o["title"]


def _search_result_by(query: str, author_part: str) -> tuple[bool | None, str, str]:
    """Public truth: the first YouTube search result for `query` whose channel contains `author_part`."""
    import urllib.parse
    try:
        html = _yt_get("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}))
        for vid in list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', html)))[:8]:
            author, title = _yt_title(vid)
            if author_part.lower() in author.lower():
                return True, title, ""
        return None, "", "no matching result on the public search page"
    except Exception as e:
        return None, "", f"could not fetch the public search page: {type(e).__name__}"


def search_result_playing(query: str, author_part: str) -> V.Verifier:
    async def fn(c):
        ok, title, why = _search_result_by(query, author_part)
        if ok is None:
            return None, why
        wins = [w["title"] for w in await V._approved_windows(c)]
        hit = [w for w in wins if title.lower()[:30] in w.lower()]
        return bool(hit), f"expected {title[:60]!r}; window: {hit[0][:60] if hit else 'none matches'}"
    return V.external(f"playing the {author_part} video for '{query}' (checked against YouTube search)", fn)


def newest_video_matches(channel_url: str) -> V.Verifier:
    async def fn(c):
        ok, title, why = _fetch_newest_title(channel_url)
        if ok is None:
            return None, why
        t = title.lower()[:40]
        wins = [w["title"] for w in await V._approved_windows(c)]      # only approved windows are ever read
        hit = [w for w in wins if t and t in w.lower()]
        if not hit and _LAST_NEWEST_ID[0]:          # YouTube may show a different title variant: the video id is the truth
            for w in await V._approved_windows(c):
                try:
                    u = c.backend.window_url(w["title"]) or ""
                except Exception:
                    u = ""
                if _LAST_NEWEST_ID[0] in u:
                    hit = [w["title"]]
                    break
        return bool(hit), f"newest public title {title[:50]!r}; matching window: {hit[0][:60] if hit else 'none'}"
    return V.external("playing the channel's NEWEST video (checked against the public page)", fn)


# ----------------------------------------------------------------------------------------------- sim suite
def sim_suite() -> list[TaskSpec]:
    def yt():
        return youtube_world(NEWEST_BATTLEBOTS)[0]

    def calc():
        page, _ = calculator_page()
        from .sim import SimBackend
        return SimBackend({}, {"calc": page})
    newest = NEWEST_BATTLEBOTS[0][1]
    return [
        TaskSpec("sim.youtube_newest", "Open youtube and play the latest video from battle bots", "web",
                 [V.completed(), V.window_title_has(newest, label="the NEWEST video is open"), V.max_steps(12), V.grounded()],
                 windows=["YouTube"], hosts=["youtube.com"], world=yt, max_actions=20, tags=frozenset({"sim"})),
        TaskSpec("sim.calculator", "Open Calculator and add 7 and 5, then tell me the result", "desktop",
                 [V.completed(), V.control_value("Calculator", {"id": "CalculatorResults"}, contains="12"),
                  V.result_has("12"), V.never_used("click"), V.max_steps(14)],
                 windows=["Calculator"], approved_apps={"calc": "calc.exe"}, world=calc, max_actions=20,
                 tags=frozenset({"sim"})),
        TaskSpec("sim.file_roundtrip", "Create the file {sandbox}/note.txt containing exactly: hello eval. "
                 "Then read it back and tell me what it says.", "files",
                 [V.completed(), V.file_text("note.txt", equals="hello eval"), V.result_has("hello eval"), V.max_steps(6)],
                 world=lambda: __import__("taskpilot.evals.sim", fromlist=["SimBackend"]).SimBackend({}),
                 max_actions=10, tags=frozenset({"sim"})),
    ]


# ----------------------------------------------------------------------------------------------- live suite
def _send_only(p: dict) -> bool:
    args = (p.get("params") or {}).get("args") or {}
    return p.get("tool") == "ui.click" and "send" in str(args.get("name", "")).lower()


def live_suite() -> list[TaskSpec]:
    yt_channel = "https://www.youtube.com/@BattleBots/videos"
    chat = lambda site, host, win: TaskSpec(           # noqa: E731
        f"live.chat_{site}", f"Open {site} and ask what is the best cheapest 25 kg Servo motor i can find", "chat",
        [V.completed(), V.result_has("servo", label="answer is about servos"),
         V.result_matches(r"(₹|rs\.?|inr|\$|usd|eur|€)\s?\d|\d[\d,]*\s?(rs|inr|usd|dollars|rupees)", "answer includes a price"),
         V.result_min(150), V.grounded(), V.max_steps(26)],
        windows=[win], hosts=[host], approve=_send_only, max_actions=34, timeout_s=420,
        tags=frozenset({"live", "network", "chat", "creates_content", "heavy"}))
    return [
        TaskSpec("live.calculator", "Open Calculator and add 7 and 5, then tell me the result", "desktop",
                 [V.completed(), V.control_value("Calculator", {"id": "CalculatorResults"}, contains="12"),
                  V.result_has("12"), V.max_steps(14), V.few_refusals(2)],
                 windows=["Calculator"], approved_apps={"calc": r"C:\Windows\System32\calc.exe"}, max_actions=20,
                 timeout_s=150, tags=frozenset({"live", "gui"})),
        TaskSpec("live.file_roundtrip", "Create the file {sandbox}/note.txt containing exactly: hello eval. "
                 "Then read it back and tell me what it says.", "files",
                 [V.completed(), V.file_text("note.txt", equals="hello eval"), V.result_has("hello eval"), V.max_steps(6)],
                 max_actions=10, timeout_s=90, tags=frozenset({"live"})),
        TaskSpec("live.youtube_newest", "Open youtube and play the latest video from battle bots", "web",
                 [V.completed(), newest_video_matches(yt_channel), V.max_steps(14), V.few_refusals(3), V.grounded()],
                 windows=["YouTube", "BattleBots"], hosts=["youtube.com"], max_actions=30, timeout_s=300,
                 tags=frozenset({"live", "network", "gui"})),
        TaskSpec("live.news", "Research on what is the new AI news today", "research",
                 [V.completed(), V.result_min(250), V.result_matches(r"\bAI\b|artificial intelligence|OpenAI|Anthropic|Nvidia|Google",
                                                                      "result is about AI news"),
                  V.used("ui.read_page", "ui.snapshot", "ui.read"), V.max_steps(16), V.grounded()],
                 windows=["Google News"], max_actions=26, timeout_s=300, tags=frozenset({"live", "network", "gui"})),
        TaskSpec("live.youtube_ssundeeplus", "Can you open youtube and play the latest video from Ssundeeplus", "web",
                 [V.completed(), newest_video_matches("https://www.youtube.com/@SSundeePlus/videos"), V.max_steps(25), V.grounded()],
                 windows=["YouTube", "SSundee"], hosts=["youtube.com"], max_actions=40, timeout_s=420,
                 tags=frozenset({"live", "network", "gui"})),
        TaskSpec("live.networkchuck_paperclip", "Open youtube and play the video from NetworkChuck where he talks about the paperclip",
                 "web", [V.completed(), search_result_playing("NetworkChuck paperclip", "NetworkChuck"), V.max_steps(25), V.grounded()],
                 windows=["YouTube", "Paperclip", "NetworkChuck"], hosts=["youtube.com"], max_actions=40, timeout_s=420,
                 tags=frozenset({"live", "network", "gui"})),
        chat("chatgpt", "chatgpt.com", "ChatGPT"),
        chat("claude", "claude.ai", "Claude"),
    ]


def get_suite(name: str) -> list[TaskSpec]:
    return {"sim": sim_suite, "live": live_suite}[name]()
