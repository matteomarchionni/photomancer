"""Build the panel icons from src/photomancer/assets/logo_source.jpg (round logo on white).

Finds the circle (non-white bounding box), crops it square and replaces the
white background with transparency through an anti-aliased circular mask.
Run once after changing the logo:  python tools/make_icon.py
"""
import os

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "src", "photomancer", "assets")
SRC = os.path.join(OUT, "logo_source.jpg")


def cut_out(path: str) -> Image.Image:
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(int)
    # the outer teal ring defines the circle (the white glow around it must not count)
    teal = (a[..., 1] - a[..., 0] > 35) & (a[..., 2] - a[..., 0] > 25)
    ys, xs = np.nonzero(teal)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = max(x1 - x0, y1 - y0) / 2
    box = (int(cx - r), int(cy - r), int(cx + r) + 1, int(cy + r) + 1)
    sq = im.crop(box).convert("RGBA")
    big = 4 * sq.width                                # supersampled mask = smooth edge
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, big - 3, big - 3), fill=255)
    sq.putalpha(mask.resize(sq.size, Image.Resampling.LANCZOS))
    return sq


if __name__ == "__main__":
    logo = cut_out(SRC)
    base = logo.resize((512, 512), Image.Resampling.LANCZOS)
    base.save(os.path.join(OUT, "photomancer_icon_512.png"))
    for n in (32, 40, 48, 56, 64, 72, 96, 128, 256):
        base.resize((n, n), Image.Resampling.LANCZOS).save(os.path.join(OUT, f"photomancer_icon_{n}.png"))
    base.save(os.path.join(OUT, "photomancer_icon.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icons written to", OUT, "from", SRC)
