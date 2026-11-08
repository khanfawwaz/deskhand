"""Generate the app icons (run once; the PNGs are committed). python scripts/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "taskpilot" / "web" / "icons"
OUT.mkdir(parents=True, exist_ok=True)


def icon(size: int, maskable: bool) -> Image.Image:
    s = size * 4                                              # draw big, shrink for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGB", (s, s))
    px = grad.load()
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            px[x, y] = (int(0x50 + (0x79 - 0x50) * t), int(0x4C + (0x74 - 0x4C) * t), int(0xA5 + (0xC3 - 0xA5) * t))
    mask = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(mask)
    if maskable:
        d.rectangle((0, 0, s, s), fill=255)                   # full bleed: the OS applies its own shape
    else:
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    pad = 0.30 if maskable else 0.24                           # keep the glyph inside the maskable safe zone
    a, b = s * pad, s * (1 - pad)
    w = b - a
    arrow = [(a, a), (b, a + w * 0.47), (a + w * 0.47, a + w * 0.57), (a + w * 0.40, b)]   # a paper-plane / pointer
    ImageDraw.Draw(img).polygon(arrow, fill=(0xD1, 0xBD, 0xC5, 255))
    return img.resize((size, size), Image.LANCZOS)


for name, size, maskable in (("icon-192.png", 192, False), ("icon-512.png", 512, False), ("icon-maskable-512.png", 512, True)):
    icon(size, maskable).save(OUT / name, optimize=True)
    print("wrote", OUT / name)
