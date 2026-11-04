"""PPT 图片"本地 OCR 优先 + 像素预过滤"（2026-11-04）。

目标：PPTX 抽取出的图片先走本地 OCR（免费），文字充足的截图不再调付费视觉模型；
像素过小的装饰图直接跳过。PDF 内嵌图维持原有视觉优先行为不变。
"""
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document
from PIL import Image

from rag.enrichment.multimodal_enricher import MultimodalEnricher

OCR_TEXT = "这是幻灯片截图里的正文内容，字数超过阈值，应当直接入库。"


def _make_image(path, size):
    Image.new("RGB", size, "white").save(path)
    return path


def _pptx_image_doc(path):
    return Document(page_content="", metadata={
        "modality": "image",
        "image_path": str(path),
        "origin_file_type": "pptx",
        "source": "课件.pptx",
    })


def _vision_provider():
    provider = MagicMock()
    provider.analyze_image.return_value = {
        "provider_used": "qwen-vision",
        "model_used": "qwen-vl-plus",
        "vision_status": "success",
        "description": "示意图",
        "ocr_text": "",
        "is_content": True,
    }
    return provider


def test_pptx_image_ocr_first_skips_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "1")
    img = _make_image(tmp_path / "a.png", (400, 300))  # 尺寸唯一，避免缓存串扰
    doc = _pptx_image_doc(img)
    provider = _vision_provider()
    enricher = MultimodalEnricher(vision_provider=provider)
    with patch("rag.loaders.pdf_ocr.ocr_image", return_value=OCR_TEXT):
        out = enricher.enrich_images([doc])[0]
    provider.analyze_image.assert_not_called()
    assert out.page_content == OCR_TEXT
    assert out.metadata["vision_status"] == "ocr_local"
    assert out.metadata["provider_used"] == "local-ocr"
    assert out.metadata["is_content"] is True


def test_pptx_image_short_ocr_falls_back_to_vision(tmp_path, monkeypatch):
    """OCR 字数不足阈值时，仍转视觉模型（图表/示意图路径）。"""
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "1")
    img = _make_image(tmp_path / "b.png", (401, 300))
    doc = _pptx_image_doc(img)
    provider = _vision_provider()
    enricher = MultimodalEnricher(vision_provider=provider)
    with patch("rag.loaders.pdf_ocr.ocr_image", return_value="短"):
        out = enricher.enrich_images([doc])[0]
    provider.analyze_image.assert_called_once()
    assert out.metadata["vision_status"] == "success"


def test_pixel_prefilter_skips_tiny_image(tmp_path, monkeypatch):
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "1")
    monkeypatch.setenv("VISION_MIN_IMAGE_PIXELS", "200000")  # 400×300=12万 < 20万
    img = _make_image(tmp_path / "c.png", (400, 300))
    doc = _pptx_image_doc(img)
    provider = _vision_provider()
    enricher = MultimodalEnricher(vision_provider=provider)
    with patch("rag.loaders.pdf_ocr.ocr_image") as ocr:
        out = enricher.enrich_images([doc])[0]
    provider.analyze_image.assert_not_called()
    ocr.assert_not_called()
    assert out.metadata["vision_status"] == "skipped"
    assert out.metadata["vision_skip_reason"] == "tiny_image_pixels"
    assert out.page_content == ""


def test_non_pptx_image_keeps_vision_first(tmp_path, monkeypatch):
    """PDF 内嵌图不抢先 OCR，维持原行为。"""
    monkeypatch.setenv("PPTX_IMAGE_OCR_FIRST", "1")
    img = _make_image(tmp_path / "d.png", (402, 300))
    doc = Document(page_content="", metadata={
        "modality": "image",
        "image_path": str(img),
        "origin_file_type": "pdf",
        "source": "教材.pdf",
    })
    provider = _vision_provider()
    enricher = MultimodalEnricher(vision_provider=provider)
    with patch("rag.loaders.pdf_ocr.ocr_image") as ocr:
        out = enricher.enrich_images([doc])[0]
    ocr.assert_not_called()
    provider.analyze_image.assert_called_once()
    assert out.metadata["vision_status"] == "success"
