"""Self-hosted ID-card OCR (OpenCV + Tesseract). No third-party API involved."""
from app.ocr.engine import read_id_card, tesseract_available
from app.ocr.extract import DEFAULT_TEMPLATE

__all__ = ["read_id_card", "tesseract_available", "DEFAULT_TEMPLATE"]
