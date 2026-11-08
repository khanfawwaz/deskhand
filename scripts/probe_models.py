"""Probe which hosted models your key can actually call, and which really see images.

For each candidate: (1) a tiny text request, (2) a request with a drawn red square asking its colour.
Writes bench_results/probe.md. The key is read from NVIDIA_API_KEY and never printed or saved.

    python scripts/probe_models.py [model-id ...]      # default: built-in candidate list
"""
import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

BASE = "https://integrate.api.nvidia.com/v1"
TIMEOUT = 60

# Names suggest capability; none of this is verified until the probe runs.
CANDIDATES = [
    # likely vision / multimodal
    "nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "google/gemma-4-31b-it", "meta/muse-glimmer-30b", "moonshotai/kimi-k2.6", "z-ai/glm-5.3-flash",
    "nvidia/nemotron-3.5-bolt-e4b", "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/cosmos-reason2-8b",
    "google/diffusiongemma-26b-a4b-it",
    # likely text-only, tool-calling candidates
    "openai/gpt-oss-20b", "nvidia/nemotron-nano-3-30b-a3b", "nvidia/nemotron-3-super-120b-a12b",
    "deepseek-ai/deepseek-v4.1-flash", "minimaxai/minimax-m3.1-flash", "poolside/laguna-xs-2.1",
    "mistralai/mistral-large-4-1t-a52b", "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/llama-3.1-nemotron-70b-instruct",
    "mistralai/mistral-large-2-instruct", "stepfun-ai/step-5-preview",
]


def red_square_b64() -> str:
    img = Image.new("RGB", (192, 192), "white")
    img.paste((220, 0, 0), (48, 48, 144, 144))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def call(c: httpx.AsyncClient, key: str, model: str, content, max_tokens=200):
    t0 = time.monotonic()
    try:
        r = await c.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                         json={"model": model, "max_tokens": max_tokens, "temperature": 0,
                               "messages": [{"role": "user", "content": content}]})
    except httpx.TimeoutException:
        return "timeout", time.monotonic() - t0, ""
    except httpx.HTTPError as e:
        return f"net:{type(e).__name__}", time.monotonic() - t0, ""
    dt = time.monotonic() - t0
    if r.status_code != 200:
        return f"HTTP {r.status_code}", dt, ""
    m = (r.json().get("choices") or [{}])[0].get("message", {})
    return "ok", dt, (m.get("content") or m.get("reasoning_content") or "")


async def probe(c, key, model, sem, img):
    async with sem:
        s1, t1, o1 = await call(c, key, model, 'Reply with only this JSON: {"ok": true}', 300)
        row = {"model": model, "text": s1, "text_s": t1,
               "json_ok": '"ok"' in o1 and "true" in o1.lower(), "image": "-", "sees": "-", "img_s": None}
        if s1 == "ok":
            s2, t2, o2 = await call(c, key, model, [
                {"type": "text", "text": "What colour is the square? Answer with one word."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}], 400)
            row.update(image=s2, img_s=t2, sees="red" if "red" in o2.lower() else ("no" if s2 == "ok" else "-"))
        print(f"  {model:<48} text={row['text']:<10} image={row['image']:<10} sees={row['sees']}", flush=True)
        return row


async def main():
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        sys.exit("set NVIDIA_API_KEY in this terminal first")
    models = sys.argv[1:] or CANDIDATES
    img, sem = red_square_b64(), asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        rows = await asyncio.gather(*(probe(c, key, m, sem, img) for m in models))
    f = lambda v: "-" if v is None else f"{v:.1f}"
    out = ["| model | text | text s | json | image | sees red | image s |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['model']} | {r['text']} | {f(r['text_s'])} | {'yes' if r['json_ok'] else 'no'} | "
                   f"{r['image']} | {r['sees']} | {f(r['img_s'])} |")
    Path("bench_results").mkdir(exist_ok=True)
    Path("bench_results/probe.md").write_text("\n".join(out), encoding="utf-8")
    Path("bench_results/probe.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print("\nsaved bench_results/probe.md")


asyncio.run(main())
