"""Hosted model backend for any OpenAI-compatible API (NVIDIA API catalog, Groq, xAI, vLLM...)."""
from __future__ import annotations

import asyncio
import os

import httpx

from .model import ModelContext, ModelRuntime, OllamaRuntime, render_user_message
from .prompts import build_system_prompt


class RateLimited(Exception):
    pass


class OpenAICompatRuntime(ModelRuntime):
    """Screenshots leave the machine, so they are sent ONLY when allow_images=True.

    The API key is read from an environment variable and never logged or placed in errors.
    """

    def __init__(self, model: str, base_url: str, api_key_env: str, allow_images: bool = False,
                 timeout: float = 120, max_retries: int = 4, json_mode: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None, prompt_vision: bool | None = None):
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"environment variable {api_key_env} is not set")
        self.model, self.base_url, self.allow_images = model, base_url.rstrip("/"), allow_images
        self.timeout, self.max_retries, self.json_mode = timeout, max_retries, json_mode
        # Reasoning models spend output tokens on hidden thinking; on a long page snapshot 1500 was all used up and
        # the visible answer came back EMPTY. Start generous and double (up to max_budget) if that still happens.
        self.max_tokens, self.max_budget = 4000, 8000
        self._key, self._transport = key, transport
        # A text-only decider in a two-model setup still has to know that screens exist (a vision model describes them).
        self.prompt_vision = prompt_vision

    async def describe(self, ctx: ModelContext) -> str:
        """The eyes: turn the attached screenshot into exact text for a decision-maker that cannot see it."""
        if not (ctx.screenshot_b64 and self.allow_images):
            return ""
        ask = ("You are the eyes of a computer-control agent. The reader CANNOT see the image. Describe this screenshot "
               "exactly and completely: which window/app is in front and its title; every visible control, link, button, "
               "field and menu with its exact visible text and the (x, y) pixel position of its CENTER in this image; any "
               "dialog, popup, error, login wall, captcha or loading state; and the main readable text. Do not guess or "
               f"add anything you cannot see. Be compact. The user's goal, for relevance only: {ctx.goal[:300]}")
        body = {"model": self.model, "temperature": 0.0, "max_tokens": 2500, "stream": False,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": ask},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{ctx.screenshot_b64}"}}]}]}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as c:
            for attempt in range(self.max_retries + 1):
                r = await c.post(f"{self.base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {self._key}"})
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt == self.max_retries:
                        raise RateLimited(f"HTTP {r.status_code} after {attempt + 1} attempts")
                    await asyncio.sleep(min(2.0 * 2 ** attempt, 30))
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"vision model API error HTTP {r.status_code}: {r.text[:200]}")
                msg = (r.json()["choices"][0].get("message") or {})
                return (msg.get("content") or "").strip()[:6000]
        return ""

    async def propose(self, ctx: ModelContext) -> str:
        vision_prompt = self.allow_images if self.prompt_vision is None else self.prompt_vision
        ctx.vision = vision_prompt               # tell the prompt/user message whether screens can exist
        text = render_user_message(ctx)
        if ctx.screenshot_b64 and self.allow_images:
            content: str | list = [
                {"type": "text", "text": text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{ctx.screenshot_b64}"}}]
        else:
            content = text + ("" if vision_prompt else
                              "\n(No screenshot is provided: images are disabled. "
                              "Use tools and text state only.)")
        body: dict = {"model": self.model, "temperature": 0.1, "max_tokens": self.max_tokens, "stream": False,
                      "messages": [{"role": "system", "content": build_system_prompt(vision=vision_prompt)},
                                   {"role": "user", "content": content}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        delay = 2.0
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as c:
            for attempt in range(self.max_retries + 1):
                r = await c.post(f"{self.base_url}/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {self._key}"})
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt == self.max_retries:
                        raise RateLimited(f"HTTP {r.status_code} after {attempt + 1} attempts")
                    wait = delay
                    try:
                        wait = max(wait, float(r.headers.get("retry-after", 0)))
                    except ValueError:
                        pass
                    await asyncio.sleep(min(wait, 60))
                    delay *= 2
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"model API error HTTP {r.status_code}: {r.text[:200]}")
                choice = r.json()["choices"][0]
                msg = choice.get("message") or {}
                content = msg.get("content") or ""
                if not content.strip():
                    if choice.get("finish_reason") == "length" and body["max_tokens"] < self.max_budget:
                        body["max_tokens"] = min(body["max_tokens"] * 2, self.max_budget)   # ran out while thinking
                        continue
                    thinking = msg.get("reasoning_content") or ""
                    return thinking if "{" in thinking else ""         # salvage a JSON answer left in the reasoning
                return content
        raise RuntimeError("unreachable")


class SplitRuntime(ModelRuntime):
    """Two models, each doing what it is best at.

    * the DECIDER (a large text/reasoning model) chooses every next step from text: the app's controls, page text,
      history, notes;
    * the EYES (a vision model) are used only when a screenshot is attached.
        mode "describe": the vision model writes the screen up as exact text and the decider chooses the step
                         (best reasoning, two calls on screenshot steps only);
        mode "route":    the vision model chooses the step itself on those steps (one call, weaker reasoning).
    Most steps need no screenshot (controls and page text come as text), so the decider handles nearly everything.
    If the vision model fails, the decider carries on without the picture rather than stopping the task.
    """

    def __init__(self, decider: ModelRuntime, eyes: OpenAICompatRuntime, mode: str = "describe"):
        if mode not in ("describe", "route"):
            raise ValueError("mode must be 'describe' or 'route'")
        self.decider, self.eyes, self.mode = decider, eyes, mode

    async def propose(self, ctx: ModelContext) -> str:
        if ctx.screenshot_b64 and self.mode == "route":
            return await self.eyes.propose(ctx)
        if ctx.screenshot_b64:                            # describe: the eyes write it up, the decider decides
            try:
                ctx.screen_text = await self.eyes.describe(ctx)
            except Exception:
                ctx.screen_text = ""
            if not ctx.screen_text:
                try:
                    return await self.eyes.propose(ctx)  # no description possible: let the vision model decide
                except Exception:
                    pass                                 # vision is down: decide without the picture
            ctx.screenshot_b64 = None
        return await self.decider.propose(ctx)


def make_runtime(provider: str, model: str, *, host: str = "http://127.0.0.1:11434",
                 base_url: str = "https://integrate.api.nvidia.com/v1",
                 api_key_env: str = "NVIDIA_API_KEY", allow_cloud_images: bool = False,
                 timeout: float = 300) -> ModelRuntime:
    if provider == "ollama":
        return OllamaRuntime(model, host, timeout)
    if provider == "openai":
        return OpenAICompatRuntime(model, base_url, api_key_env, allow_cloud_images, timeout)
    raise ValueError(f"unknown provider '{provider}'")


def make_split_runtime(text_model: str, vision_model: str, *, base_url: str = "https://integrate.api.nvidia.com/v1",
                       api_key_env: str = "NVIDIA_API_KEY", mode: str = "describe", timeout: float = 300) -> SplitRuntime:
    decider = OpenAICompatRuntime(text_model, base_url, api_key_env, allow_images=False, timeout=timeout, prompt_vision=True)
    eyes = OpenAICompatRuntime(vision_model, base_url, api_key_env, allow_images=True, timeout=timeout)
    return SplitRuntime(decider, eyes, mode)
