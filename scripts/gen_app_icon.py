"""Build the ST SyncTool app icon from the brand master.

The brand master is ``assets/app_icon_master.png`` (the 4096px export). It is
not committed (too large); when present it is downsampled to the committed
``assets/app_icon.png`` (1024px runtime/dock icon). The ``.icns`` for the
packaged .app is always (re)built from ``app_icon.png``.

Re-run after dropping in a new master:  python scripts/gen_app_icon.py
"""
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
MASTER = ASSETS / "app_icon_master.png"
ICON_PX = 1024


def build() -> Path:
    png = ASSETS / "app_icon.png"
    if MASTER.exists():
        img = Image.open(MASTER).convert("RGBA")
        if img.size != (ICON_PX, ICON_PX):
            img = img.resize((ICON_PX, ICON_PX), Image.LANCZOS)
        img.save(png)
    elif not png.exists():
        raise SystemExit(
            f"Neither the brand master ({MASTER.name}) nor {png.name} is present.")
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
