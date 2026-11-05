"""Cache identity for effective image parsing policy (no model initialization)."""
import hashlib
import json
import os

DEFAULTS = {
    "VISION_MAX_IMAGES_PER_DOC": "20",
    "VISION_MAX_IMAGES_PER_TEXT_PDF": "4",
    "VISION_MAX_IMAGES_PER_SEMI_TEXT_PDF": "8",
    "VISION_TEXT_PDF_CHARS_PER_PAGE": "100",
    "VISION_TEXT_PDF_MID_CHARS_PER_PAGE": "50",
    "VISION_MIN_IMAGE_PIXELS": "40000",
    "VISION_MIN_IMAGE_BYTES": "8192",
    "VISION_DOC_BUDGET_SECONDS": "180",
    "VISION_IMAGE_TIMEOUT_SECONDS": "30",
}


def image_policy_signature():
    values = {key: str(float(os.getenv(key) or default)) for key, default in DEFAULTS.items()}
    # Use the same effective defaults as the production PPT image enricher.
    from rag.enrichment.multimodal_enricher import _pptx_ocr_first_enabled, _pptx_ocr_min_chars
    values["pptx_ocr_first"] = _pptx_ocr_first_enabled()
    values["pptx_ocr_min_chars"] = _pptx_ocr_min_chars()
    values["strategy_version"] = "pdf-density-v2-pptx-ocr-v1"
    values["vision_mode"] = os.getenv("VISION_MODE", "vision")
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]
