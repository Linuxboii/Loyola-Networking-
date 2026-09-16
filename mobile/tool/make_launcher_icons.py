"""Generate the Android launcher icon from the app's own mark.

The Flutter template ships a generic blue Flutter logo, which is the single
clearest signal that something is a side project rather than a product. This
renders the same "LA" monogram the app draws on its splash and sign-in screens
into every density Android asks for, plus the adaptive-icon foreground layer
that modern launchers mask into whatever shape the device theme uses.

    python mobile/tool/make_launcher_icons.py

Requires Pillow. Re-run it only if the brand colour or the monogram changes;
the output is committed so a normal build needs neither Python nor Pillow.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RES = Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "res"

# Matches AppTheme._seed in lib/core/theme.dart.
BRAND = (27, 58, 107, 255)
INK = (255, 255, 255, 255)
MONOGRAM = "LA"

# Legacy square icon: one PNG per density bucket.
LEGACY = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}

# Adaptive foreground: 108dp canvas, but only the middle 72dp is guaranteed
# visible once a launcher applies its mask, so the monogram is drawn small and
# centred inside that safe zone.
ADAPTIVE = {
    "mipmap-mdpi": 108,
    "mipmap-hdpi": 162,
    "mipmap-xhdpi": 216,
    "mipmap-xxhdpi": 324,
    "mipmap-xxxhdpi": 432,
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise SystemExit(
        "No bold TrueType font found. Add one to FONT_CANDIDATES and re-run."
    )


def draw_monogram(image: Image.Image, box: int, offset_x: int, offset_y: int) -> None:
    draw = ImageDraw.Draw(image)
    font = load_font(int(box * 0.46))
    left, top, right, bottom = draw.textbbox((0, 0), MONOGRAM, font=font)
    draw.text(
        (
            offset_x + (box - (right - left)) / 2 - left,
            offset_y + (box - (bottom - top)) / 2 - top,
        ),
        MONOGRAM,
        font=font,
        fill=INK,
    )


def render_legacy(size: int) -> Image.Image:
    """A rounded square, drawn at 4x and downsampled so the corners stay smooth."""
    scale = 4
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (0, 0, size * scale - 1, size * scale - 1),
        radius=int(size * scale * 0.22),
        fill=BRAND,
    )
    draw_monogram(canvas, size * scale, 0, 0)
    return canvas.resize((size, size), Image.LANCZOS)


def render_adaptive_foreground(size: int) -> Image.Image:
    """Transparent 108dp canvas with the monogram inside the 72dp safe zone."""
    scale = 4
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    safe = int(size * scale * 72 / 108)
    inset = (size * scale - safe) // 2
    draw_monogram(canvas, safe, inset, inset)
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> int:
    if not RES.exists():
        print(f"not found: {RES}", file=sys.stderr)
        return 1

    written = 0
    for folder, size in LEGACY.items():
        target = RES / folder
        target.mkdir(parents=True, exist_ok=True)
        render_legacy(size).save(target / "ic_launcher.png")
        written += 1

    for folder, size in ADAPTIVE.items():
        target = RES / folder
        target.mkdir(parents=True, exist_ok=True)
        render_adaptive_foreground(size).save(target / "ic_launcher_foreground.png")
        written += 1

    print(f"wrote {written} icon files under {RES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
