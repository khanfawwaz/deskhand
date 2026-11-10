"""Build the Chrome Web Store package and listing images for the Deskhand extension.

    python scripts/build_extension.py

Writes  dist/extension/deskhand-extension-<version>.zip  (upload this) and  dist/extension/store/*.png  (screenshots,
promo tile, marquee) using a headless Chrome with a throwaway profile (never your real one).
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "extension"
OUT = ROOT / "dist" / "extension"
sys.path.insert(0, str(ROOT / "src"))


def chrome() -> str:
    from taskpilot.agent.cdp import chrome_path
    p = chrome_path()
    if not p:
        raise SystemExit("Chrome not found (needed to render the store images)")
    return p


def build_zip() -> Path:
    man = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    z = OUT / f"deskhand-extension-{man['version']}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for p in sorted(SRC.rglob("*")):
            if p.is_file():
                f.write(p, p.relative_to(SRC).as_posix())             # manifest.json must be at the root of the zip
    return z


CSS = """
:root{--bg:#07071D;--surface:#11102b;--line:#363255;--text:#efe8ec;--muted:#b3a6bf;--primary:#7974C3;--good:#74c9a0;--warn:#e3b07a;--bad:#e0795c}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 700px at 80% -10%,#1d1a4d 0,var(--bg) 60%);color:var(--text);
font:16px/1.5 "Segoe UI",system-ui,sans-serif;overflow:hidden}
.wrap{display:flex;align-items:center;gap:72px;height:100%;padding:0 90px}
.copy{flex:1}.copy h1{font-size:54px;line-height:1.08;margin:0 0 20px;letter-spacing:-.5px}.copy p{font-size:22px;color:var(--muted);margin:0 0 12px}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:6px 14px;color:var(--muted);font-size:15px;margin:6px 8px 0 0}
.popup{width:340px;background:var(--bg);border:1px solid var(--line);border-radius:18px;box-shadow:0 30px 80px rgba(0,0,0,.55);overflow:hidden;flex:none}
.popup header{display:flex;align-items:center;gap:10px;padding:16px;border-bottom:1px solid var(--line)}
.popup header img{width:30px;height:30px;border-radius:8px}.popup header b{font-size:18px}
.popup main{padding:16px;display:grid;gap:14px}
.status{display:flex;gap:10px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px}
.dot{width:10px;height:10px;border-radius:50%;margin-top:6px;flex:none}.status strong{display:block}.status span{color:var(--muted);font-size:14px}
.btn{background:var(--primary);color:#07071D;font-weight:700;text-align:center;padding:11px;border-radius:10px}
.quiet{border:1px solid var(--line);color:var(--muted);text-align:center;padding:10px;border-radius:10px}
.small{color:var(--muted);font-size:13px;margin:0}
.tile{display:flex;align-items:center;gap:22px;height:100%;padding:0 34px}.tile img{width:96px;height:96px;border-radius:22px}
.tile h2{margin:0;font-size:42px}.tile p{margin:4px 0 0;color:var(--muted);font-size:19px}
"""


def popup(state: str, logo: str) -> str:
    dot, title, detail, btn = {
        "connected": ("var(--good)", "Connected to Deskhand", "Ready for your tasks.", '<div class="quiet">Disconnect this browser</div>'),
        "pairing": ("var(--warn)", "Waiting for your approval", "Click Yes in the Deskhand window on your computer.", ""),
        "searching": ("var(--muted)", "Not connected", "Open the Deskhand app on this computer, then press Connect.",
                      '<div class="btn">Connect to Deskhand</div>'),
    }[state]
    return (f'<div class="popup"><header><img src="{logo}"><b>Deskhand</b></header><main>'
            f'<div class="status"><i class="dot" style="background:{dot}"></i><div><strong>{title}</strong><span>{detail}</span></div></div>'
            f'{btn}<p class="small">Deskhand only controls this browser while you have asked it to do a task. It never sees your '
            f'passwords, and it only talks to the Deskhand app on this computer.</p></main></div>')


def page(body: str) -> str:
    return f"<!doctype html><meta charset=utf-8><style>{CSS}html,body{{height:100%}}</style>{body}"


def render(html: str, out: Path, w: int, h: int) -> None:
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "p.html"
        f.write_text(html, encoding="utf-8")
        subprocess.run([chrome(), "--headless=new", "--disable-gpu", f"--user-data-dir={Path(t) / 'profile'}", "--hide-scrollbars",
                        f"--window-size={w},{h}", f"--screenshot={out}", f.as_uri()], check=True, timeout=90,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_images() -> list[Path]:
    store = OUT / "store"
    store.mkdir(parents=True, exist_ok=True)
    logo = "data:image/png;base64," + base64.b64encode((SRC / "icons" / "icon128.png").read_bytes()).decode()
    shots = [
        ("1-ask-from-anywhere", "Ask Deskhand. It uses your own Chrome.",
         "Play the latest video from a channel you follow, open your Netflix, look something up. Deskhand does it in the "
         "browser you are already signed in to.", "connected"),
        ("2-you-stay-in-control", "You approve the connection.",
         "Connecting needs a Yes from you on your computer, once. Disconnect whenever you like from this popup.", "pairing"),
        ("3-private-by-design", "Stays on your computer.",
         "The extension only talks to the Deskhand app on your computer. It never reads your passwords or cookies and never "
         "types credentials: if a site asks you to sign in, it asks you.", "searching"),
    ]
    out = []
    for name, h1, p, state in shots:
        html = page(f'<div class="wrap"><div class="copy"><h1>{h1}</h1><p>{p}</p><span class="pill">Works in your signed-in Chrome</span>'
                    f'<span class="pill">Asks before it connects</span></div>{popup(state, logo)}</div>')
        f = store / f"screenshot-{name}.png"
        render(html, f, 1280, 800)
        out.append(f)
    tile = page(f'<div class="tile"><img src="{logo}"><div><h2>Deskhand</h2><p>Your computer, on request.</p></div></div>')
    render(tile, store / "promo-tile-440x280.png", 440, 280)
    marquee = page(f'<div class="tile" style="padding:0 120px;gap:40px"><img style="width:180px;height:180px;border-radius:40px" src="{logo}">'
                   f'<div><h2 style="font-size:84px">Deskhand</h2><p style="font-size:34px">Ask. It does it in your own Chrome.</p></div></div>')
    render(marquee, store / "marquee-1400x560.png", 1400, 560)
    return out + [store / "promo-tile-440x280.png", store / "marquee-1400x560.png"]


if __name__ == "__main__":
    z = build_zip()
    print("package:", z, f"({z.stat().st_size // 1024} KB)")
    if "--no-images" not in sys.argv:
        for p in build_images():
            print("image:  ", p)
