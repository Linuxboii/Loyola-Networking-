"""Generate synthetic ID cards to exercise the OCR pipeline.

These are stand-ins until real Loyola Academy cards are available. They imitate
the usual Indian college layout: institution banner, portrait, labelled fields.
Variations (rotation, blur, glare, perspective) let us check the pipeline copes
with a phone snapshot rather than a flatbed scan.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1012, 638  # ~CR80 at 300dpi, landscape


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def render_card(
    name="ANANYA RAJESH KUMAR",
    roll="21BSC1042",
    course="B.SC (MPCS)",
    dept="COMPUTER SCIENCE",
    # Dates are kept in the future on purpose: an expired card is a hard fail in
    # the verification flow, so a fixture with a past expiry tests the rejection
    # path rather than the happy path.
    batch=f"{dt.date.today().year}-{dt.date.today().year + 3}",
    valid=f"30/06/{dt.date.today().year + 3}",
    label_roll="Roll No",
) -> Image.Image:
    img = Image.new("RGB", (W, H), (252, 252, 250))
    d = ImageDraw.Draw(img)

    # Header band
    d.rectangle([0, 0, W, 118], fill=(18, 62, 122))
    d.text((150, 22), "LOYOLA ACADEMY", font=_font(40, True), fill=(255, 255, 255))
    d.text((150, 72), "Degree & P.G. College, Alwal, Secunderabad", font=_font(19), fill=(214, 226, 244))
    d.ellipse([34, 18, 118, 102], fill=(255, 255, 255), outline=(18, 62, 122), width=3)
    d.text((58, 46), "LA", font=_font(34, True), fill=(18, 62, 122))

    d.text((W // 2 - 110, 132), "STUDENT IDENTITY CARD", font=_font(24, True), fill=(160, 30, 40))

    # Portrait placeholder that the face detector can plausibly find
    px, py, pw, ph = 56, 190, 210, 260
    d.rectangle([px, py, px + pw, py + ph], fill=(226, 230, 236), outline=(120, 130, 145), width=2)
    cx, cy = px + pw // 2, py + 96
    d.ellipse([cx - 58, cy - 58, cx + 58, cy + 58], fill=(206, 176, 150))
    d.ellipse([cx - 26, cy - 18, cx - 12, cy - 4], fill=(60, 45, 40))
    d.ellipse([cx + 12, cy - 18, cx + 26, cy - 4], fill=(60, 45, 40))
    d.arc([cx - 26, cy + 4, cx + 26, cy + 40], start=10, end=170, fill=(90, 60, 50), width=4)
    d.ellipse([cx - 78, cy + 70, cx + 78, cy + 210], fill=(70, 96, 140))

    rows = [
        ("Name", name),
        (label_roll, roll),
        ("Course", course),
        ("Department", dept),
        ("Batch", batch),
        ("Valid Upto", valid),
    ]
    x_label, x_value, y = 300, 470, 196
    for label, value in rows:
        d.text((x_label, y), label, font=_font(22), fill=(70, 78, 90))
        d.text((x_label + 148, y), ":", font=_font(22), fill=(70, 78, 90))
        d.text((x_value, y), value, font=_font(24, True), fill=(16, 20, 28))
        y += 47

    d.line([56, 500, 266, 500], fill=(120, 130, 145), width=2)
    d.text((92, 506), "Signature", font=_font(16), fill=(120, 130, 145))
    d.text((700, 560), "Principal", font=_font(20, True), fill=(40, 46, 58))
    d.rectangle([0, H - 26, W, H], fill=(18, 62, 122))
    return img


def distort(img: Image.Image, mode: str, seed: int = 7) -> np.ndarray:
    random.seed(seed)
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    if mode == "clean":
        return arr

    if mode in {"perspective", "phone"}:
        h, w = arr.shape[:2]
        pad = int(w * 0.18)
        canvas = cv2.copyMakeBorder(arr, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(58, 62, 68))
        H2, W2 = canvas.shape[:2]
        src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
        j = int(w * 0.055)
        dst = np.float32(
            [
                [pad + random.randint(0, j), pad + random.randint(0, j)],
                [pad + w - random.randint(0, j), pad + random.randint(0, j)],
                [pad + w - random.randint(0, j), pad + h - random.randint(0, j)],
                [pad + random.randint(0, j), pad + h - random.randint(0, j)],
            ]
        )
        m = cv2.getPerspectiveTransform(src, dst)
        canvas = cv2.warpPerspective(canvas, m, (W2, H2), borderValue=(58, 62, 68))
        arr = canvas

    if mode == "phone":
        arr = cv2.GaussianBlur(arr, (3, 3), 0)
        noise = np.random.normal(0, 6, arr.shape).astype(np.int16)
        arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        arr = cv2.convertScaleAbs(arr, alpha=0.92, beta=-6)

    if mode == "rotated":
        arr = cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)

    if mode == "glare":
        overlay = arr.copy()
        h, w = arr.shape[:2]
        cv2.ellipse(overlay, (int(w * 0.62), int(h * 0.36)), (int(w * 0.20), int(h * 0.16)), 25, 0, 360, (255, 255, 255), -1)
        arr = cv2.addWeighted(overlay, 0.42, arr, 0.58, 0)

    if mode == "dim":
        arr = cv2.convertScaleAbs(arr, alpha=0.55, beta=-12)

    return arr


VARIANTS = ["clean", "perspective", "phone", "rotated", "glare", "dim"]


def main(outdir: str = "/tmp/loyola-cards") -> None:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    card = render_card()
    for v in VARIANTS:
        arr = distort(card, v)
        cv2.imwrite(str(out / f"card_{v}.jpg"), arr, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    # A second identity to prove the extractor is not memorising one layout.
    alt = render_card(
        name="MOHAMMED IRFAN ALI",
        roll="22BCA/0317",
        course="BCA",
        dept="COMPUTER APPLICATIONS",
        batch=f"{dt.date.today().year}-{dt.date.today().year + 3}",
        valid=f"Valid upto June {dt.date.today().year + 3}",
        label_roll="Admission No",
    )
    cv2.imwrite(str(out / "card_alt.jpg"), distort(alt, "phone", seed=11), [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    print(f"wrote {len(VARIANTS) + 1} cards to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/loyola-cards")
