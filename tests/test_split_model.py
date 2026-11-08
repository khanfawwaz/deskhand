"""Two models: a strong text model decides, a vision model looks. Checked with a fake HTTP API."""
import json

import httpx
import pytest

from taskpilot.hosted import OpenAICompatRuntime, SplitRuntime
from taskpilot.model import ModelContext


def ctx(shot=None):
    return ModelContext(goal="Open Notepad", state="running", history=[], screenshot_b64=shot,
                        screenshot_size=(1280, 720) if shot else None, active_window="Desktop")


class Api:
    """A fake chat API: records every request, answers per model name."""
    def __init__(self, fail_vision=False):
        self.calls, self.fail_vision = [], fail_vision

    def transport(self, name, reply):
        def handler(req: httpx.Request):
            body = json.loads(req.content)
            self.calls.append((name, body))
            if self.fail_vision and name == "eyes":
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
        return httpx.MockTransport(handler)


def make(api, mode="describe", monkeypatch=None):
    monkeypatch.setenv("K", "x")
    decider = OpenAICompatRuntime("big-text", "http://x", "K", allow_images=False, prompt_vision=True,
                                  transport=api.transport("decider", '{"action": {"tool": "capture_screen"}}'), max_retries=0)
    eyes = OpenAICompatRuntime("seer", "http://x", "K", allow_images=True,
                               transport=api.transport("eyes", "Notepad window in front. Button 'Save' at (640, 400)."), max_retries=0)
    return SplitRuntime(decider, eyes, mode)


async def test_steps_without_a_screenshot_go_to_the_big_text_model_only(monkeypatch):
    api = Api()
    out = await make(api, monkeypatch=monkeypatch).propose(ctx())
    assert "capture_screen" in out and [n for n, _ in api.calls] == ["decider"]
    sys_prompt = api.calls[0][1]["messages"][0]["content"]
    assert "capture_screen" in sys_prompt and "No screenshots are available" not in sys_prompt   # it knows screens exist
    assert "images are disabled" not in json.dumps(api.calls[0][1])


async def test_describe_mode_eyes_write_it_up_and_the_big_model_decides_without_seeing_pixels(monkeypatch):
    api = Api()
    out = await make(api, "describe", monkeypatch).propose(ctx(shot="AAAA"))
    assert [n for n, _ in api.calls] == ["eyes", "decider"]
    eyes_body, decider_body = api.calls[0][1], api.calls[1][1]
    assert "image_url" in json.dumps(eyes_body) and eyes_body["model"] == "seer"
    d = json.dumps(decider_body)
    assert "image_url" not in d and decider_body["model"] == "big-text"                  # the decider never gets the image
    assert "Button 'Save' at (640, 400)" in d and "described_below" in d                # ...it gets the description
    assert "capture_screen" in out


async def test_route_mode_lets_the_vision_model_decide_on_screenshot_steps(monkeypatch):
    api = Api()
    await make(api, "route", monkeypatch).propose(ctx(shot="AAAA"))
    assert [n for n, _ in api.calls] == ["eyes"] and "image_url" in json.dumps(api.calls[0][1])


async def test_if_the_vision_model_is_down_the_big_model_carries_on(monkeypatch):
    api = Api(fail_vision=True)
    out = await make(api, "describe", monkeypatch).propose(ctx(shot="AAAA"))
    assert api.calls[-1][0] == "decider" and "image_url" not in json.dumps(api.calls[-1][1]) and "capture_screen" in out


def test_mode_is_validated(monkeypatch):
    monkeypatch.setenv("K", "x")
    r = OpenAICompatRuntime("m", "http://x", "K")
    with pytest.raises(ValueError):
        SplitRuntime(r, r, "nope")
