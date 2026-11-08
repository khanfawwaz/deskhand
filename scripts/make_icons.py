"""Generate the app icons from the logo code (run when the logo changes). python scripts/make_icons.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from taskpilot.branding import draw_icon  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "src" / "taskpilot" / "web" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

for name, size, kw in (("icon-192.png", 192, {}), ("icon-512.png", 512, {}),
                       ("icon-maskable-512.png", 512, {"margin": 0.12})):        # maskable: keep art in the safe zone
    draw_icon(size, **kw).save(OUT / name, optimize=True)
    print("wrote", OUT / name)
