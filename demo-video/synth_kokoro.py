"""Local text-to-speech with Kokoro (Apache-2.0, runs on this computer, nothing is sent anywhere).

  python synth_kokoro.py --jobs jobs.json [--voice af_heart] [--speed 1.08]     jobs.json = [{"text": "...", "out": "x.wav"}, ...]
  python synth_kokoro.py --text "Hello" --out hello.wav --voice af_bella
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

here = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--jobs")
ap.add_argument("--text")
ap.add_argument("--out")
ap.add_argument("--voice", default="af_heart")
ap.add_argument("--speed", type=float, default=1.08)
ap.add_argument("--lang", default="en-us")
a = ap.parse_args()

k = Kokoro(str(here / "models" / "kokoro-v1.0.int8.onnx"), str(here / "models" / "voices-v1.0.bin"))
jobs = json.loads(Path(a.jobs).read_text(encoding="utf-8")) if a.jobs else [{"text": a.text, "out": a.out}]
for j in jobs:
    voice = j.get("voice", a.voice)
    samples, rate = k.create(j["text"], voice=voice, speed=j.get("speed", a.speed), lang=a.lang)
    tail = np.zeros(int(rate * 0.12), dtype=samples.dtype)          # a breath of silence at the end of each line
    sf.write(j["out"], np.concatenate([samples, tail]), rate, subtype="PCM_16")
    print(j["out"], f"{len(samples) / rate:.1f}s", file=sys.stderr)
