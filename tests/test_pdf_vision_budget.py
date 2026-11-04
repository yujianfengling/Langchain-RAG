"""PDF 视觉预算回归测试（2026-11-04）

正文型 PDF 不再逐张图片调视觉：文本密度越高，单文档视觉预算越小；
扫描件/图片型 PDF 仍保留完整预算。
"""

from pathlib import Path

import fitz

from rag.loaders.pdf_loader import PdfLoader


def _make_pdf(path: Path, text: str, pages: int = 4) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def test_text_pdf_gets_small_vision_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_DOC", "20")
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_TEXT_PDF", "4")
    monkeypatch.setenv("VISION_TEXT_PDF_CHARS_PER_PAGE", "100")
    pdf = _make_pdf(tmp_path / "text.pdf", "材料力学 " * 40)

    loader = PdfLoader()
    assert loader._text_chars_per_page(pdf) >= 100
    assert loader._vision_image_limit(pdf) == 4


def test_mixed_pdf_gets_medium_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_DOC", "20")
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_TEXT_PDF", "4")
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_SEMI_TEXT_PDF", "8")
    monkeypatch.setenv("VISION_TEXT_PDF_CHARS_PER_PAGE", "100")
    monkeypatch.setenv("VISION_TEXT_PDF_MID_CHARS_PER_PAGE", "50")
    pdf = _make_pdf(tmp_path / "mixed.pdf", "图 " * 30)  # ~60 字符/页

    loader = PdfLoader()
    assert loader._vision_image_limit(pdf) == 8


def test_blank_pdf_keeps_full_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_DOC", "20")
    monkeypatch.setenv("VISION_MAX_IMAGES_PER_TEXT_PDF", "4")
    pdf = _make_pdf(tmp_path / "scan.pdf", "", pages=3)

    loader = PdfLoader()
    assert loader._text_chars_per_page(pdf) == 0
    assert loader._vision_image_limit(pdf) == 20
