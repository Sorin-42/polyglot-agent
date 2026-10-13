"""Render the app icon at every size it is shown at.

Small sizes are drawn on their own pixel grid (bar positions and thickness
snapped to whole pixels) instead of being shrunk from the 256 px image, which
is what made the old 16-48 px icons look soft.

    .venv\\Scripts\\python tools\\make_icons.py

Writes launcher/icon.ico (launcher + standalone exe), app/static/icons/app.ico
(app window) and app/static/icons/icon-{32,192,512}.png (page favicons).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ACCENT = (201, 100, 66, 255)      # #c96442, same as the UI accent
WHITE = (255, 255, 255, 255)
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
SS = 8                            # supersampling factor


def render(size: int) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded square; tiny sizes use the full tile so the shape stays readable
    pad = 0 if size <= 24 else round(size * 0.03)
    d.rounded_rectangle([pad * SS, pad * SS, (size - pad) * SS - 1, (size - pad) * SS - 1],
                        radius=round(size * 0.22) * SS, fill=ACCENT)
    # three bars (long, short, medium), snapped to whole pixels at this size
    t = max(2, round(size * 0.075))                  # bar thickness
    gap = max(t + 2, round(size * 0.156))            # distance between bar tops
    x0 = round(size * 0.24)
    lengths = [round(size * 0.52), round(size * 0.36), round(size * 0.46)]
    top = (size - (2 * gap + t)) // 2
    for i, length in enumerate(lengths):
        y = top + i * gap
        # square ends up to 32 px: round caps only add blurry half-pixels there
        d.rounded_rectangle([x0 * SS, y * SS, (x0 + length) * SS - 1, (y + t) * SS - 1],
                            radius=0 if size <= 32 else t * SS // 2, fill=WHITE)
    return img.resize((size, size), Image.BOX)   # area average: no ringing halos


def main() -> None:
    frames = {n: render(n) for n in ICO_SIZES}
    big = frames[256]
    others = [frames[n] for n in ICO_SIZES if n != 256]
    icons = ROOT / "app" / "static" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / "launcher" / "icon.ico", icons / "app.ico"):
        big.save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=others)
        print("wrote", path.relative_to(ROOT))
    for n in (32, 192, 512):
        (frames.get(n) or render(n)).save(icons / f"icon-{n}.png", optimize=True)
        print("wrote", (icons / f"icon-{n}.png").relative_to(ROOT))


if __name__ == "__main__":
    main()
