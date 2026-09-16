"""Image conditioning for ID-card OCR.

Everything here is classical CV — OpenCV + Tesseract, no cloud API, no ML model
download. The job is to turn a phone snapshot taken in a corridor into something
Tesseract can read: find the card, flatten it, fix the rotation, and produce a
handful of binarisation variants so the recogniser gets more than one shot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

# A typical Indian college ID card is close to CR80 (85.6 x 54 mm) in landscape,
# but plenty are portrait. We normalise the long edge instead of assuming either.
TARGET_LONG_EDGE = 1400
MIN_LONG_EDGE = 500


@dataclass
class QualityReport:
    width: int
    height: int
    blur_score: float
    brightness: float
    glare_ratio: float
    problems: list[str] = field(default_factory=list)

    @property
    def acceptable(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "blur_score": round(self.blur_score, 1),
            "brightness": round(self.brightness, 1),
            "glare_ratio": round(self.glare_ratio, 4),
            "problems": self.problems,
        }


def decode(data: bytes) -> np.ndarray | None:
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        return None
    return img


def glare_ratio(gray: np.ndarray) -> float:
    """Fraction of the frame lost to a specular hotspot.

    Counting every pixel brighter than 250 does not measure glare, it measures
    how much white card is in the picture: a clean capture of a white ID card
    scored 0.637 and one ruined by a torch reflection scored 0.639, so the
    check never once told the two apart. What actually marks a reflection is
    that it is *locally* brighter than the rest of the card — so blur the print
    away to leave the illumination field, then look for a region standing clear
    of the card's own paper level.
    """
    small = downscale(gray, 256).astype(np.float32)
    illum = cv2.GaussianBlur(small, (0, 0), 6)
    paper = float(np.median(illum))
    hot = (illum >= paper + 25) & (illum >= 240)
    return float(hot.sum()) / float(hot.size)


def assess(img: np.ndarray) -> QualityReport:
    """Reject unusable captures before spending CPU on OCR."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    glare = glare_ratio(gray)

    problems: list[str] = []
    if max(h, w) < MIN_LONG_EDGE:
        problems.append("too_small")
    if blur < 45:
        problems.append("blurry")
    if brightness < 45:
        problems.append("too_dark")
    # Measured on the card rather than the whole frame, a correctly exposed
    # white ID card sits in the 200-235 range on its own — the old 225 ceiling
    # was calibrated against captures diluted by whatever desk they were taken
    # on, and now flags good photos. Past 242 there is no ink left to read.
    elif brightness > 242:
        problems.append("too_bright")
    if glare > 0.06:
        problems.append("glare")
    return QualityReport(w, h, blur, brightness, glare, problems)


def assess_card(card: np.ndarray, original: np.ndarray) -> QualityReport:
    """Re-measure quality once the card has been found and flattened.

    ``assess`` has to run on the raw upload so a too-small capture is rejected
    before any CPU is spent on it, but every other number it produces there is
    measured over whatever else was in the frame — the desk, a sleeve, the dark
    border around a phone snapshot. Brightness gets dragged toward the
    background and the glare test compares the card against a median that is
    not the card's, which is how a plain grey desk used to make a perfectly
    exposed card look like it was covered in reflections. The size verdict
    still comes from the original capture, because that is what the "hold the
    card closer" advice refers to.
    """
    rep = assess(card)
    h, w = original.shape[:2]
    rep.width, rep.height = w, h
    rep.problems = [p for p in rep.problems if p != "too_small"]
    if max(h, w) < MIN_LONG_EDGE:
        rep.problems.insert(0, "too_small")
    return rep


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Return corners as top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _as_quad(cnt: np.ndarray) -> np.ndarray | None:
    """Reduce a contour to four corners, tolerantly.

    A single fixed epsilon is brittle: a card photographed at an angle, with a
    slightly rounded corner or a nick in the edge, approximates to five or six
    points and gets rejected. The detector then settles on some strong inner
    rectangle instead — on an ID card that is usually the white body below the
    coloured header, which silently crops the institution's name off the top.
    """
    hull = cv2.convexHull(cnt)
    peri = cv2.arcLength(hull, True)
    for eps in (0.02, 0.03, 0.04, 0.05, 0.07):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx
    # Still not a clean quad — fall back to the minimum enclosing rectangle,
    # which is a fine approximation of a card seen at a modest angle.
    rect = cv2.minAreaRect(hull)
    box = cv2.boxPoints(rect)
    if cv2.contourArea(box) > cv2.contourArea(hull) * 1.35:
        return None  # contour is not rectangle-shaped at all
    return box.reshape(4, 1, 2).astype(np.int32)


def detect_card(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """Find the card quadrilateral and flatten it.

    Returns (image, cropped). Falls back to the original frame when no
    convincing quad is found — a tight hand-held crop is common and perfectly
    readable, so failure here is not fatal.
    """
    h, w = img.shape[:2]
    scale = 900.0 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img.copy()

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 60, 60)
    edged = cv2.Canny(gray, 40, 130)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]
    frame_area = small.shape[0] * small.shape[1]

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < frame_area * 0.18:
            break
        # A quad that is essentially the whole frame is the photo's own border,
        # not the card. Accepting it silently skips cropping altogether, which
        # leaves the background in and costs both accuracy and OCR time.
        if area > frame_area * 0.93:
            continue

        approx = _as_quad(cnt)
        if approx is None:
            continue

        quad = _order_corners(approx.reshape(4, 2).astype("float32"))
        if scale < 1:
            quad /= scale

        (tl, tr, br, bl) = quad
        width = max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))
        height = max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))
        if width < 120 or height < 80:
            continue
        aspect = max(width, height) / max(1.0, min(width, height))
        # Anything wildly off a card's proportions is probably a desk edge.
        if aspect > 2.4:
            continue

        dst = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype="float32",
        )
        m = cv2.getPerspectiveTransform(quad, dst)
        warped = cv2.warpPerspective(img, m, (int(width), int(height)))
        return warped, True

    return img, False


def deskew(img: np.ndarray) -> np.ndarray:
    """Straighten small rotations using the dominant text angle."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 50:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if angle > 45:
        angle = angle - 90
    if abs(angle) < 0.4 or abs(angle) > 20:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def normalise_size(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge == 0:
        return img
    scale = TARGET_LONG_EDGE / long_edge
    if 0.95 < scale < 1.6:
        return img
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


VARIANT_NAMES = ("clahe", "adaptive", "otsu", "invert", "sharp")


def _base_gray(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    # bilateralFilter keeps glyph edges while killing sensor noise, and costs a
    # few milliseconds where fastNlMeansDenoising costs several seconds. On a
    # single core that difference is the whole latency budget.
    return cv2.bilateralFilter(gray, 7, 45, 45)


def prepare(img: np.ndarray) -> np.ndarray:
    """Denoise + local contrast once; every variant is derived from this."""
    return cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(_base_gray(img))


def make_variant(img: np.ndarray, name: str, clahe: np.ndarray | None = None) -> np.ndarray | None:
    """Build one binarisation on demand.

    Tesseract's accuracy on a given card swings wildly with preprocessing, so we
    try several — but lazily, stopping as soon as a reading is confident.
    """
    if clahe is None:
        clahe = prepare(img)

    if name == "clahe":
        return clahe
    if name == "otsu":
        return cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    if name == "adaptive":
        adaptive = cv2.adaptiveThreshold(
            clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
        )
        # Cards print over coloured bands; a light open removes the band's
        # speckle without eating thin strokes.
        return cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    if name == "invert":
        # The institution's name is usually white-on-navy in the header band.
        # Every dark-text binarisation erases it, which then fails the issuer
        # check. This variant is what reads the banner.
        inv = cv2.bitwise_not(clahe)
        return cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    if name == "sharp":
        return cv2.filter2D(clahe, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))
    return None


def variants(img: np.ndarray) -> list[tuple[str, np.ndarray]]:
    clahe = prepare(img)
    out = []
    for name in VARIANT_NAMES:
        arr = make_variant(img, name, clahe)
        if arr is not None:
            out.append((name, arr))
    return out


def top_band(img: np.ndarray, fraction: float = 0.42, long_edge: int = 640) -> np.ndarray:
    """The header strip, where the institution's name is printed.

    Downscaled deliberately: we only need to recognise a handful of large
    banner words, and OCR time is roughly linear in area.
    """
    h = img.shape[0]
    band = img[: max(1, int(h * fraction)), :]
    edge = max(band.shape[:2])
    if edge > long_edge:
        scale = long_edge / edge
        band = cv2.resize(band, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return band


def downscale(img: np.ndarray, long_edge: int) -> np.ndarray:
    edge = max(img.shape[:2])
    if edge <= long_edge:
        return img
    scale = long_edge / edge
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def find_face(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate the portrait on the card so reviewers see a face crop instead of
    the whole card (PRD 5.3: reviewers get a redacted view)."""
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            return None
    except Exception:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    # Haar cost scales with pixel count, and a portrait on an ID card is huge
    # relative to the frame. Detect on a thumbnail and scale the box back up —
    # this is the difference between 2 seconds and 150 milliseconds per card.
    long_edge = max(gray.shape[:2])
    ds = min(1.0, 480.0 / long_edge) if long_edge else 1.0
    small = cv2.resize(gray, None, fx=ds, fy=ds, interpolation=cv2.INTER_AREA) if ds < 1.0 else gray
    small = cv2.equalizeHist(small)

    faces = cascade.detectMultiScale(small, scaleFactor=1.12, minNeighbors=5, minSize=(28, 28))
    if len(faces) == 0:
        faces = cascade.detectMultiScale(small, scaleFactor=1.06, minNeighbors=3, minSize=(22, 22))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    if ds < 1.0:
        inv = 1.0 / ds
        x, y, w, h = int(x * inv), int(y * inv), int(w * inv), int(h * inv)
    pad = int(0.22 * max(w, h))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img.shape[1], x + w + pad)
    y1 = min(img.shape[0], y + h + pad)
    return x0, y0, x1 - x0, y1 - y0


def crop(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = box
    return img[y : y + h, x : x + w]


def portrait_region(img: np.ndarray) -> tuple[int, int, int, int]:
    """Where the photo sits when face detection comes up empty.

    A reviewer still needs to see a face rather than the whole card, so fall
    back to the conventional portrait block: left third, upper two thirds.
    """
    h, w = img.shape[:2]
    if w >= h:  # landscape card — portrait on the left
        return 0, int(h * 0.12), int(w * 0.34), int(h * 0.74)
    return int(w * 0.18), int(h * 0.14), int(w * 0.64), int(h * 0.42)


def encode_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else b""


def rotations(img: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Cards get photographed sideways constantly. Cheap to try all four."""
    return [
        (0, img),
        (90, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)),
        (180, cv2.rotate(img, cv2.ROTATE_180)),
        (270, cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)),
    ]


def estimate_text_orientation(img: np.ndarray) -> float:
    """Rough proxy for 'does this look like upright text' — the ratio of wide
    horizontal connected components. Used to shortlist rotations before OCR."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (18, 3))
    dilated = cv2.dilate(thresh, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    wide = 0
    total = 0
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 180:
            continue
        total += 1
        if w > h * 1.6:
            wide += 1
    if total == 0:
        return 0.0
    return wide / total


def math_safe_round(v: float) -> float:
    return 0.0 if math.isnan(v) or math.isinf(v) else round(v, 4)
