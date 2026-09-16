"""The OCR entry point.

``read_id_card`` is a pure function: bytes in, plain-dict out. That keeps it
safe to run inside a ``ProcessPoolExecutor``, which is how the web app calls it —
OpenCV and Tesseract never get resident in the uvicorn worker on a 1 vCPU box.

Budget matters. A naive sweep of 4 rotations x 4 binarisations x 3 page-seg
modes is 48 Tesseract invocations and roughly a minute of CPU. Instead we settle
orientation once (cheaply), then run a small grid and merge results field by
field, taking the most confident reading of each. That lands around 3-6 seconds.
"""
from __future__ import annotations

import time
from typing import Any

import pytesseract
from PIL import Image

from app.config import settings
from app.ocr import extract as fx
from app.ocr import imageops as io

# Windows installers frequently leave tesseract off PATH. Honour an explicit
# path when one is configured; otherwise pytesseract's default ("tesseract")
# resolves through PATH as it should on a Linux server.
if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

# Page segmentation modes worth trying on a card: a uniform block, then a
# single column. Sparse text (11) is held back for cards where those find
# nothing, because it is the slowest and the noisiest.
PSM_GRID = [6, 4]
PSM_FALLBACK = 11
VARIANT_GRID = ["clahe", "adaptive", "otsu"]

# tessedit_do_invert=0 disables Tesseract's automatic second pass on an inverted
# copy. We already feed it an explicit inverted variant where that matters, and
# on a single core the hidden retry roughly doubles recognition time.
TESS_BASE = "--oem 1 -c preserve_interword_spaces=1 -c tessedit_do_invert=0"


def _to_pil(arr) -> Image.Image:
    if arr.ndim == 2:
        return Image.fromarray(arr)
    import cv2

    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def _words_from(arr, psm: int) -> list[fx.Word]:
    cfg = f"{TESS_BASE} --psm {psm}"
    try:
        data = pytesseract.image_to_data(_to_pil(arr), config=cfg, output_type=pytesseract.Output.DICT)
    except Exception:
        return []
    words: list[fx.Word] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        words.append(
            fx.Word(
                text=text,
                conf=conf,
                line=int(data["line_num"][i]),
                block=int(data["block_num"][i]),
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
            )
        )
    return words


def _settle_rotation(img):
    """Decide which way is up without paying for four full OCR passes."""
    try:
        # Orientation detection does not need full resolution, and at full size
        # it costs as much as a real recognition pass.
        osd = pytesseract.image_to_osd(_to_pil(io.downscale(img, 800)), config="--psm 0")
        for line in osd.splitlines():
            if line.lower().startswith("rotate:"):
                deg = int(line.split(":")[1].strip()) % 360
                if deg:
                    import cv2

                    # Tesseract reports how far the image must be turned
                    # *clockwise* to come out upright.
                    mapping = {
                        90: cv2.ROTATE_90_CLOCKWISE,
                        180: cv2.ROTATE_180,
                        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
                    }
                    if deg in mapping:
                        return cv2.rotate(img, mapping[deg]), deg, "osd"
                return img, 0, "osd"
    except Exception:
        pass

    # OSD needs a decent amount of text and often refuses on a sparse card.
    # Fall back to the horizontal-run-length heuristic.
    best = (0.0, 0, img)
    for deg, rot in io.rotations(img):
        score = io.estimate_text_orientation(rot)
        if score > best[0]:
            best = (score, deg, rot)
    return best[2], best[1], "heuristic"


# Where to look for the institution's banner, in the order worth paying for.
# Each entry is (which image, top fraction, long edge the band is scaled to).
# The banner is white-on-navy, so every dark-text binarisation erases it and
# only the inverted variant can read it — the old second attempt used "clahe"
# and scored zero on all ten fixtures while costing a full Tesseract pass.
# What actually decides the matter is resolution: a 640px band is plenty on a
# frame that is mostly card, and illegible on a phone snapshot where the card
# fills two thirds of a wider frame.
ISSUER_BANDS = [
    ("full", 0.42, 640),
    ("full", 0.42, 1000),
    ("card", 0.42, 640),
]


def _settle_issuer(full, card, tpl: dict[str, Any]) -> tuple[bool, int, list[str], int]:
    """Decide whether the card names the institution, and say what it cost.

    This runs before the field grid because a failed issuer check costs a 45%
    confidence penalty, which on its own drops a good card below the autopass
    threshold *and* defeats the grid's early exit — so one unreadable banner
    used to turn a five-second read into an eighteen-second one that still went
    to manual review.

    The banner is read from the uncropped frame first: card detection can
    legitimately trim to the body panel, and the institution's name lives above
    it. The cropped card is the last resort, for the opposite case — a frame
    with so much background that the banner is small in it.
    """
    passes = 0
    best: tuple[bool, int, list[str]] = (False, 0, [])
    for which, fraction, long_edge in ISSUER_BANDS:
        frame = full if which == "full" else card
        if frame is None:
            continue
        band = io.make_variant(io.top_band(frame, fraction=fraction, long_edge=long_edge), "invert")
        if band is None:
            continue
        words = _words_from(band, 6)
        passes += 1
        if not words:
            continue
        ok, hits, found = fx.check_issuer(fx.build_lines(words), tpl)
        if hits > best[1]:
            best = (ok, hits, found)
        if ok:
            break
    return best[0], best[1], best[2], passes


def _merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Field-level merge: for each field, keep the most confident reading."""
    if not results:
        return {}
    merged = {
        "fields": {},
        "confidence": {},
        "method": {},
        "source": {},
        "issuer_ok": any(r.get("issuer_ok") for r in results),
        "issuer_hits": max((r.get("issuer_hits", 0) for r in results), default=0),
        "issuer_found": next((r["issuer_found"] for r in results if r.get("issuer_ok")), []),
    }
    keys = ["roll_number", "full_name", "course", "department", "batch", "expiry", "year_codes"]
    empty_for: dict[str, Any] = {"expiry": None, "year_codes": []}
    winners: dict[str, dict[str, Any] | None] = {}
    for key in keys:
        best_conf = -1.0
        best_val: Any = None
        best_method = "none"
        best_src = ""
        best_res: dict[str, Any] | None = None
        for r in results:
            conf = float(r["confidence"].get(key, 0.0))
            val = r["fields"].get(key)
            if val in (None, "", []):
                continue
            if conf > best_conf:
                best_conf, best_val, best_method, best_src = conf, val, r["method"].get(key, "?"), r.get("_tag", "")
                best_res = r
        merged["fields"][key] = best_val if best_val is not None else empty_for.get(key, "")
        merged["confidence"][key] = round(max(best_conf, 0.0), 3)
        merged["method"][key] = best_method
        merged["source"][key] = best_src
        winners[key] = best_res

    # batch_year is derived from the batch string, so it has to come from the
    # result that actually won "batch". Matching on the string after the fact
    # picked an arbitrary result whenever no batch was found at all, and left
    # the derived year with no confidence or method of its own.
    batch_winner = winners.get("batch")
    merged["fields"]["batch_year"] = batch_winner["fields"].get("batch_year") if batch_winner else None
    merged["confidence"]["batch_year"] = merged["confidence"].get("batch", 0.0)
    merged["method"]["batch_year"] = merged["method"].get("batch", "none")
    merged["source"]["batch_year"] = merged["source"].get("batch", "")

    # "batch" is the one field where the most confident reading is not the best
    # one. It is derived from the year-code rows printed under the course, so a
    # pass that found all three rows knows the real span even if it read them
    # less crisply than a pass that found two — and on a phone snapshot that is
    # exactly what happens, with the sharper two-row read winning and silently
    # shortening the student's course by a year.
    spans = []
    for r in results:
        if r["method"].get("batch") != "year-codes":
            continue
        raw = r["fields"].get("batch") or ""
        end = r["fields"].get("batch_year")
        if not end or "-" not in raw:
            continue
        try:
            start = int(raw.split("-", 1)[0])
        except ValueError:
            continue
        # A degree runs a few years. Anything else is a misread year, not a
        # longer course, and must not be allowed to widen the span.
        if 1 <= int(end) - start <= 6:
            spans.append((int(end), float(r["confidence"].get("batch", 0.0)), r))

    if spans:
        end, _conf, widest = max(spans, key=lambda t: (t[0], t[1]))
        if end > int(merged["fields"].get("batch_year") or 0):
            for key in ("batch", "year_codes"):
                merged["fields"][key] = widest["fields"].get(key)
                merged["confidence"][key] = round(float(widest["confidence"].get(key, 0.0)), 3)
                merged["method"][key] = widest["method"].get(key, "?")
                merged["source"][key] = widest.get("_tag", "")
            merged["fields"]["batch_year"] = widest["fields"].get("batch_year")
            merged["confidence"]["batch_year"] = merged["confidence"]["batch"]
            merged["method"]["batch_year"] = merged["method"]["batch"]
            merged["source"]["batch_year"] = merged["source"]["batch"]

    richest = max(results, key=lambda r: len(r.get("text", "")))
    merged["text"] = richest.get("text", "")
    merged["lines"] = richest.get("lines", [])
    return merged


def read_id_card(
    image_bytes: bytes,
    template: dict[str, Any] | None = None,
    autopass_at: float = 0.72,
    want_face: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline. Never raises — failures come back as ``ok: False``."""
    started = time.time()
    out: dict[str, Any] = {
        "ok": False,
        "error": None,
        "quality": {},
        "fields": {},
        "confidence": {},
        "method": {},
        "overall": 0.0,
        "text": "",
        "lines": [],
        "issuer_ok": False,
        "card_jpeg": None,
        "face_jpeg": None,
        "elapsed_ms": 0,
        "passes": 0,
        "rotation": 0,
    }

    img = io.decode(image_bytes)
    if img is None:
        out["error"] = "unreadable_image"
        return out

    quality = io.assess(img)
    out["quality"] = quality.as_dict()
    if "too_small" in quality.problems:
        out["error"] = "image_too_small"
        return out

    # Orientation is settled on the whole frame first, so the crop step and the
    # issuer check both work on an upright image.
    full = io.normalise_size(img)
    full, rotation, rot_how = _settle_rotation(full)
    full = io.deskew(full)

    card, cropped = io.detect_card(full)
    card = io.normalise_size(card)
    # Now that the card has been found, re-measure quality on the card alone.
    # Everything but the size verdict was previously computed over the desk and
    # the dark border around a phone snapshot as well as the card.
    out["quality"] = io.assess_card(card, img).as_dict()
    out["rotation"] = rotation
    out["rotation_method"] = rot_how
    out["card_detected"] = cropped
    out["card_jpeg"] = io.encode_jpeg(card, 88)

    if want_face:
        box = io.find_face(card)
        out["face_found"] = bool(box)
        # Reviewers get a redacted view (PRD 5.3), so always produce *some*
        # portrait crop — a conventional-position fallback beats showing them
        # the whole card because a cascade missed.
        out["face_jpeg"] = io.encode_jpeg(io.crop(card, box or io.portrait_region(card)), 88)

    clahe = io.prepare(card)
    results: list[dict[str, Any]] = []
    passes = 0
    done = False

    # Settle the issuer check up front, from the inverted header strip. The
    # institution's name is normally white-on-navy, so every dark-text
    # binarisation erases it — and a failed issuer check costs a 45% confidence
    # penalty, which would otherwise defeat the early exit and make us OCR the
    # whole grid on a perfectly good card.
    merged_tpl = {**fx.DEFAULT_TEMPLATE, **(template or {})}
    issuer_ok, issuer_hits, issuer_found, band_passes = _settle_issuer(full, card, merged_tpl)
    passes += band_passes

    for vname in VARIANT_GRID:
        arr = io.make_variant(card, vname, clahe)
        if arr is None:
            continue
        for psm in PSM_GRID:
            words = _words_from(arr, psm)
            passes += 1
            if not words:
                continue
            parsed = fx.extract_fields(words, template)
            if issuer_ok:
                parsed["issuer_ok"] = True
            parsed["_tag"] = f"{vname}/psm{psm}"
            parsed["_overall"] = fx.overall_confidence(parsed)
            results.append(parsed)

            # Good enough — stop burning the single core. The name has to clear
            # the bar on its own, not ride on the overall score: the roll
            # number carries 45% of that weight, so a pass that nailed the roll
            # and made hash of the name ("Pararil Jon Sine") used to score high
            # enough to stop the grid before a later variant read it properly.
            if (
                parsed["_overall"] >= autopass_at + 0.12
                and parsed["fields"].get("roll_number")
                and parsed["confidence"].get("full_name", 0.0) >= autopass_at + 0.10
            ):
                done = True
                break
        if done:
            break

    if not results:
        # Last resort: sparse-text mode on the highest-contrast variant.
        arr = io.make_variant(card, "adaptive", clahe)
        if arr is not None:
            words = _words_from(arr, PSM_FALLBACK)
            passes += 1
            if words:
                parsed = fx.extract_fields(words, template)
                if issuer_ok:
                    parsed["issuer_ok"] = True
                parsed["_tag"] = f"adaptive/psm{PSM_FALLBACK}"
                parsed["_overall"] = fx.overall_confidence(parsed)
                results.append(parsed)

    if not results:
        out["error"] = "no_text_found"
        out["elapsed_ms"] = int((time.time() - started) * 1000)
        out["passes"] = passes
        return out

    merged = _merge(results)
    merged["confidence"] = merged.get("confidence", {})
    if issuer_ok:
        merged["issuer_ok"] = True
        merged["issuer_hits"] = issuer_hits
        merged["issuer_found"] = issuer_found

    overall = fx.overall_confidence(merged)

    out.update(
        {
            "ok": True,
            "fields": merged["fields"],
            "confidence": merged["confidence"],
            "method": merged["method"],
            "source": merged.get("source", {}),
            "issuer_ok": merged.get("issuer_ok", False),
            "issuer_found": merged.get("issuer_found", []),
            "text": merged.get("text", ""),
            "lines": merged.get("lines", [])[:40],
            "overall": overall,
            "passes": passes,
            "best_variant": max(results, key=lambda r: r.get("_overall", 0)).get("_tag", ""),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    )
    return out


def tesseract_available() -> tuple[bool, str]:
    try:
        return True, str(pytesseract.get_tesseract_version())
    except Exception as exc:  # pragma: no cover - environment probe
        return False, str(exc)
