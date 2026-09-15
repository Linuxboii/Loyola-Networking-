"""Where does the OCR wall-clock actually go? Stage-by-stage timings."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ocr import engine, imageops as io  # noqa: E402


def main(path: str) -> None:
    data = Path(path).read_bytes()
    marks: list[tuple[str, float]] = []
    t0 = time.time()

    def mark(label: str) -> None:
        marks.append((label, (time.time() - t0) * 1000))

    img = io.decode(data)
    mark("decode")
    io.assess(img)
    mark("assess")
    card, _ = io.detect_card(img)
    mark("detect_card")
    card = io.normalise_size(card)
    mark("normalise")
    card, rot, how = engine._settle_rotation(card)
    mark(f"rotation({how}={rot})")
    card = io.deskew(card)
    mark("deskew")
    io.encode_jpeg(card, 88)
    mark("encode_card")
    io.find_face(card)
    mark("find_face")
    clahe = io.prepare(card)
    mark("prepare")
    band = io.make_variant(io.top_band(card), "invert")
    engine._words_from(band, 6)
    mark("issuer_band_ocr")
    arr = io.make_variant(card, "clahe", clahe)
    mark("variant")
    engine._words_from(arr, 6)
    mark("tesseract_psm6")

    prev = 0.0
    print(f"{Path(path).name}  ({card.shape[1]}x{card.shape[0]} after prep)")
    for label, ms in marks:
        print(f"  {label:<24} {ms - prev:8.0f} ms   (cumulative {ms:.0f})")
        prev = ms


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/loyola-cards/card_phone.jpg")
