"""The Deskhand logo, drawn in code so the same artwork serves the web icons, the tray icon and the Windows .exe icon.

Logo "D": a cursor hovering over a desk, drawn as clean line art on a light rounded square. Colours are the app palette.
Needs Pillow (a dependency of the helper and the icon script, not of the server).
"""
from __future__ import annotations

INK, VIOLET, CORAL, CREAM = (7, 7, 29), (80, 76, 165), (198, 98, 71), (209, 189, 197)
_CURSOR = [(48, 22), (48, 68), (60, 57), (69, 77), (78, 73), (69, 54), (86, 54)]
_DESK = [((22, 82), (98, 82)), ((32, 84), (32, 102)), ((88, 84), (88, 102))]       # the top, then two legs
_LINE = 7


def draw_icon(size: int, *, background: bool = True, margin: float = 0.0):
    """RGBA image, `size` px square. margin = share of the edge kept empty around the artwork (maskable icons)."""
    from PIL import Image, ImageDraw
    s = size * 4                                           # draw big, shrink for smooth edges
    k = s / 120.0
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if background:
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(28 * k), fill=CREAM + (255,))
        if margin:
            d.rectangle((0, 0, s, s), fill=CREAM + (255,))     # full bleed: the OS applies its own shape
    art = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    a = ImageDraw.Draw(art)
    r = _LINE / 2 * k
    for (x1, y1), (x2, y2) in _DESK:                       # round-capped lines
        a.line([(x1 * k, y1 * k), (x2 * k, y2 * k)], fill=INK + (255,), width=int(_LINE * k))
        for x, y in ((x1, y1), (x2, y2)):
            a.ellipse((x * k - r, y * k - r, x * k + r, y * k + r), fill=INK + (255,))
    a.polygon([(x * k, y * k) for x, y in _CURSOR], fill=VIOLET + (255,))
    cx, cy, dot = 48 * k, 22 * k, 5 * k
    a.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=CORAL + (255,))
    if margin:
        inner = int(s * (1 - 2 * margin))
        art = art.resize((inner, inner), Image.LANCZOS)
        pad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        pad.paste(art, ((s - inner) // 2, (s - inner) // 2))
        art = pad
    img = Image.alpha_composite(img, art)
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path: str, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    draw_icon(256).save(path, format="ICO", sizes=[(n, n) for n in sizes])
