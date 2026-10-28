"""托盘图标：12 芒圆角星，颜色跟随当前皮肤的强调色。"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

CORAL = (217, 119, 87)


def spark_polys(cx, cy, size):
    k = size / 100.0
    polys = []
    for i in range(12):
        a = i / 12 * math.pi * 2
        L = 34 if i % 2 else 46
        w = .09

        def p(r, t):
            return (cx + math.cos(t) * r * k, cy + math.sin(t) * r * k)
        polys.append([p(6, a - w * 2.2), p(L, a - w * .35), p(L + 3, a), p(L, a + w * .35), p(6, a + w * 2.2)])
    return polys


def tray_image(size=64, color=CORAL):
    if isinstance(color, str):
        color = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    S = size * 4
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for poly in spark_polys(S / 2, S / 2, S * 0.98):
        d.polygon(poly, fill=color + (255,))
    r = S * .10
    d.ellipse((S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r), fill=color + (255,))
    return im.resize((size, size), Image.LANCZOS)
