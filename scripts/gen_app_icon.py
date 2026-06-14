"""Generate the ST SyncTool app icon (assets/app_icon.png + .icns).

A faithful recreation of the brand mark: a charcoal rounded square with a gold
border, a gold circular sync (two chasing arrows) and a gold serif "S" centred.
Re-run after tweaking constants:  python scripts/gen_app_icon.py
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024
GOLD = (246, 190, 0, 255)       # #F6BE00
CHARCOAL = (43, 46, 48, 255)    # #2B2E30

C = SIZE // 2
R = 300
ARC_W = 74


def _serif_font(px: int) -> ImageFont.FreeTypeFont:
    for name in (
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "/System/Library/Fonts/Supplemental/Baskerville.ttc",
        "/Library/Fonts/Georgia Bold.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, px)
    return ImageFont.load_default()


def _arrow_head(draw, phi_deg: float):
    """Triangular arrowhead tangent to the ring at angle phi (clockwise from E)."""
    phi = math.radians(phi_deg)
    px, py = C + R * math.cos(phi), C + R * math.sin(phi)
    tx, ty = -math.sin(phi), math.cos(phi)      # clockwise tangent
    nx, ny = math.cos(phi), math.sin(phi)       # outward normal
    length, half = 130, 92
    tip = (px + tx * length, py + ty * length)
    left = (px + nx * half, py + ny * half)
    right = (px - nx * half, py - ny * half)
    draw.polygon([tip, left, right], fill=GOLD)


def build() -> Path:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Charcoal rounded square with a gold border.
    d.rounded_rectangle([38, 38, SIZE - 38, SIZE - 38], radius=210,
                        fill=CHARCOAL, outline=GOLD, width=36)

    # Two chasing arcs (top + bottom), leaving gaps on the sides for the heads.
    bbox = [C - R, C - R, C + R, C + R]
    d.arc(bbox, 200, 338, fill=GOLD, width=ARC_W)    # top arc (over 270° = top)
    d.arc(bbox, 20, 158, fill=GOLD, width=ARC_W)     # bottom arc (over 90° = bottom)
    _arrow_head(d, 338)    # lower-right, pointing down (clockwise)
    _arrow_head(d, 158)    # upper-left, pointing up (clockwise)

    # Centred serif S.
    font = _serif_font(560)
    box = d.textbbox((0, 0), "S", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text((C - w / 2 - box[0], C - h / 2 - box[1]), "S", font=font, fill=GOLD)

    ASSETS.mkdir(exist_ok=True)
    png = ASSETS / "app_icon.png"
    img.save(png)
    return png


def make_icns(png: Path):
    iconset = ASSETS / "app_icon.iconset"
    iconset.mkdir(exist_ok=True)
    specs = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
             (256, 1), (256, 2), (512, 1), (512, 2)]
    for base, scale in specs:
        px = base * scale
        name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
        subprocess.run(["sips", "-z", str(px), str(px), str(png),
                        "--out", str(iconset / name)],
                       check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset),
                    "-o", str(ASSETS / "app_icon.icns")], check=True)
    for f in iconset.iterdir():
        f.unlink()
    iconset.rmdir()


if __name__ == "__main__":
    png = build()
    make_icns(png)
    print(f"Wrote {png} and {ASSETS / 'app_icon.icns'}")
