"""Small local ONNX OCR used only for records already in human review."""
from __future__ import annotations

import time
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image

from app.ocr import extract as fx

_engine = None


def _engine_for_queue():
    """Create the model in the isolated process only when queue work exists."""
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def _boxes(value: Any, count: int) -> list[Any]:
    boxes = getattr(value, "boxes", None)
    if boxes is None and isinstance(value, tuple) and value:
        boxes = value[0]
    return list(boxes) if boxes is not None else [None] * count


def _texts(value: Any) -> list[str]:
    texts = getattr(value, "txts", None) or getattr(value, "texts", None)
    if texts is None and isinstance(value, tuple) and len(value) > 1:
        texts = value[1]
    return [str(text).strip() for text in (texts or []) if str(text).strip()]


def _scores(value: Any, count: int) -> list[float]:
    scores = getattr(value, "scores", None)
    if scores is None and isinstance(value, tuple) and len(value) > 2:
        scores = value[2]
    values: list[float] = []
    for score in list(scores or []):
        try:
            values.append(float(score) * (100 if float(score) <= 1 else 1))
        except (TypeError, ValueError):
            values.append(0.0)
    return values + [0.0] * max(0, count - len(values))


def read_queued_id_card(image_bytes: bytes, template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read one encrypted card using the compact local RapidOCR ONNX model."""
    started = time.monotonic()
    try:
        image = np.array(Image.open(BytesIO(image_bytes)).convert("RGB"))
        result = _engine_for_queue()(image)
        texts = _texts(result)
        if not texts:
            return {"ok": False, "error": "no_text_found", "elapsed_ms": int((time.monotonic() - started) * 1000)}

        scores = _scores(result, len(texts))
        boxes = _boxes(result, len(texts))
        words: list[fx.Word] = []
        for index, text in enumerate(texts):
            box = boxes[index] if index < len(boxes) else None
            try:
                points = np.asarray(box, dtype=float)
                left, top = int(points[:, 0].min()), int(points[:, 1].min())
                width, height = int(np.ptp(points[:, 0])), int(np.ptp(points[:, 1]))
            except (TypeError, ValueError, IndexError):
                left = top = width = height = 0
            words.append(fx.Word(text=text, conf=scores[index], line=index + 1, block=1, left=left, top=top, width=width, height=height))

        parsed = fx.extract_fields(words, template)
        return {
            "ok": True,
            "text": parsed.get("text", ""),
            "lines": parsed.get("lines", [])[:40],
            "fields": parsed.get("fields", {}),
            "confidence": parsed.get("confidence", {}),
            "overall": fx.overall_confidence(parsed),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {"ok": False, "error": f"rapidocr_failed: {exc}", "elapsed_ms": int((time.monotonic() - started) * 1000)}