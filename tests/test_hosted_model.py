import json

import httpx
import pytest

from taskpilot.bench.cases import build_suite
from taskpilot.hosted import OpenAICompatRuntime, RateLimited
from taskpilot.model import extract_json


def runtime(handler, monkeypatch, **kw):
    monkeypatch.setenv("TEST_KEY", "sekret-123")
    return OpenAICompatRuntime("m", "https://x.test/v1", "TEST_KEY", transport=httpx.MockTransport(handler), **kw)


def ok(content='{"action":{"tool":"capture_screen"}}'):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def ctx():
    return build_suite()[1].context()


async def test_images_not_sent_by_default(monkeypatch):
    seen = {}
    def h(req):
        seen["body"] = json.loads(req.content); seen["auth"] = req.headers["authorization"]
        return ok()
    rt = runtime(h, monkeypatch)
    await rt.propose(ctx())
    content = seen["body"]["messages"][1]["content"]
    assert isinstance(content, str) and "base64" not in json.dumps(seen["body"])
    assert seen["auth"] == "Bearer sekret-123"


async def test_images_sent_only_when_allowed(monkeypatch):
    seen = {}
    def h(req):
        seen["body"] = json.loads(req.content); return ok()
    await runtime(h, monkeypatch, allow_images=True).propose(ctx())
    parts = seen["body"]["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_retries_on_429_then_succeeds(monkeypatch):
    calls = []
    async def fake_sleep(s): calls.append(s)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    n = {"i": 0}
    def h(req):
        n["i"] += 1
        return httpx.Response(429, headers={"retry-after": "3"}) if n["i"] < 3 else ok()
    out = await runtime(h, monkeypatch).propose(ctx())
    assert "capture_screen" in out and n["i"] == 3 and calls[0] >= 3


async def test_gives_up_after_max_retries(monkeypatch):
    async def fake_sleep(s): pass
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    with pytest.raises(RateLimited):
        await runtime(lambda r: httpx.Response(429), monkeypatch, max_retries=2).propose(ctx())


async def test_error_does_not_leak_key(monkeypatch):
    with pytest.raises(RuntimeError) as e:
        await runtime(lambda r: httpx.Response(401, text="bad key"), monkeypatch).propose(ctx())
    assert "sekret-123" not in str(e.value)


def test_missing_key_fails_fast(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(RuntimeError, match="NOPE"):
        OpenAICompatRuntime("m", "https://x", "NOPE")


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert extract_json('Sure! Here: {"x": 1} hope that helps') == {"x": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


async def test_empty_answer_from_a_token_starved_reasoning_model_is_retried_with_more_budget(monkeypatch):
    seen = []
    def h(req):
        body = json.loads(req.content)
        seen.append(body["max_tokens"])
        if len(seen) == 1:        # all tokens went into hidden reasoning: no visible answer
            return httpx.Response(200, json={"choices": [{"finish_reason": "length",
                                                          "message": {"content": None, "reasoning_content": "thinking..."}}]})
        return ok('{"action":{"tool":"capture_screen"}}')
    out = await runtime(h, monkeypatch).propose(ctx())
    assert "capture_screen" in out and seen == [4000, 8000]


async def test_budget_is_capped_and_json_left_in_reasoning_is_salvaged(monkeypatch):
    seen = []
    def h(req):
        seen.append(json.loads(req.content)["max_tokens"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {
            "content": "", "reasoning_content": 'I think {"action": {"tool": "capture_screen"}} is right'}}]})
    out = await runtime(h, monkeypatch).propose(ctx())
    assert seen == [4000, 8000] and '"capture_screen"' in out        # never exceeds the cap; salvages the JSON


async def test_truly_empty_answer_returns_empty_text_not_a_crash(monkeypatch):
    out = await runtime(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}),
                        monkeypatch).propose(ctx())
    assert out == ""
