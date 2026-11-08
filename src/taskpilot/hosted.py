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
                 transport: httpx.AsyncBaseTransport | None = None):
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"environment variable {api_key_env} is not set")
        self.model, self.base_url, self.allow_images = model, base_url.rstrip("/"), allow_images
        self.timeout, self.max_retries, self.json_mode = timeout, max_retries, json_mode
        # Reasoning models spend output tokens on hidden thinking; on a long page snapshot 1500 was all used up and
        # the visible answer came back EMPTY. Start generous and double (up to max_budget) if that still happens.
        self.max_tokens, self.max_budget = 4000, 8000
        self._key, self._transport = key, transport

    async def propose(self, ctx: ModelContext) -> str:
        ctx.vision = self.allow_images           # tell the prompt/user message whether screens can exist
        text = render_user_message(ctx)
        if ctx.screenshot_b64 and self.allow_images:
            content: str | list = [
                {"type": "text", "text": text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{ctx.screenshot_b64}"}}]
        else:
            content = text + ("" if self.allow_images else
                              "\n(No screenshot is provided: images are disabled. "
                              "Use tools and text state only.)")
        body: dict = {"model": self.model, "temperature": 0.1, "max_tokens": self.max_tokens, "stream": False,
                      "messages": [{"role": "system", "content": build_system_prompt(vision=self.allow_images)},
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


def make_runtime(provider: str, model: str, *, host: str = "http://127.0.0.1:11434",
                 base_url: str = "https://integrate.api.nvidia.com/v1",
                 api_key_env: str = "NVIDIA_API_KEY", allow_cloud_images: bool = False,
                 timeout: float = 300) -> ModelRuntime:
    if provider == "ollama":
        return OllamaRuntime(model, host, timeout)
    if provider == "openai":
        return OpenAICompatRuntime(model, base_url, api_key_env, allow_cloud_images, timeout)
    raise ValueError(f"unknown provider '{provider}'")
