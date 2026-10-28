"""扫描件本地 OCR 的单元测试（2026-10-28）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rag.loaders import pdf_ocr


def _make_pdf(path: Path, *, text: str = "", with_image: bool = False) -> Path:
    import fitz
    from PIL import Image

    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    if text:
        page.insert_text((40, 60), text, fontsize=14)
    if with_image:
        img_path = Path(tempfile.gettempdir()) / "pdf_ocr_block.png"
        Image.new("RGB", (200, 120), "white").save(img_path)
        page.insert_image(fitz.Rect(20, 80, 220, 200), filename=str(img_path))
    doc.save(str(path))
    doc.close()
    return path


def test_select_engine_off(monkeypatch):
    monkeypatch.setenv("IMPORT_OCR_ENGINE", "off")
    name, reason = pdf_ocr.select_engine()
    assert name is None
    assert "off" in reason


def test_select_engine_prefers_available(monkeypatch):
    monkeypatch.setenv("IMPORT_OCR_ENGINE", "rapidocr")
    monkeypatch.setattr(pdf_ocr, "_rapidocr_available", lambda: True)
    assert pdf_ocr.select_engine()[0] == "rapidocr"
    monkeypatch.setattr(pdf_ocr, "_rapidocr_available", lambda: False)
    name, reason = pdf_ocr.select_engine()
    assert name is None
    assert reason


def test_detect_scanned_text_pdf(tmp_path):
    p = _make_pdf(tmp_path / "text.pdf", text="database system concepts chapter one " * 6)
    assert pdf_ocr.detect_scanned(p) is False


def test_detect_scanned_image_pdf(tmp_path):
    p = _make_pdf(tmp_path / "scan.pdf", with_image=True)
    assert pdf_ocr.detect_scanned(p) is True


def test_ocr_pdf_pages_uses_cache_and_callback(tmp_path, monkeypatch):
    p = _make_pdf(tmp_path / "scan.pdf", with_image=True)
    monkeypatch.setattr(pdf_ocr, "select_engine", lambda prefer=None: ("rapidocr", ""))
    calls = []

    def fake_ocr(image, engine):
        calls.append(engine)
        return "识别文本"

    monkeypatch.setattr(pdf_ocr, "_ocr_image", fake_ocr)
    seen = []
    out = pdf_ocr.ocr_pdf_pages(p, on_page=lambda no, text, total: seen.append((no, text)))
    assert out == {1: "识别文本"}
    assert seen == [(1, "识别文本")]
    assert calls == ["rapidocr"]

    # 第二次应命中页缓存，不再调用引擎
    calls.clear()
    out2 = pdf_ocr.ocr_pdf_pages(p)
    assert out2 == {1: "识别文本"}
    assert calls == []


def test_ocr_pdf_pages_abort(tmp_path, monkeypatch):
    import fitz

    p = tmp_path / "multi.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=200, height=200)
    doc.save(str(p))
    doc.close()

    monkeypatch.setattr(pdf_ocr, "select_engine", lambda prefer=None: ("rapidocr", ""))
    monkeypatch.setattr(pdf_ocr, "_ocr_image", lambda image, engine: "x")
    out = pdf_ocr.ocr_pdf_pages(p, should_abort=lambda: True)
    assert out == {}


def test_extract_page_text_prefers_text_layer(tmp_path, monkeypatch):
    p = _make_pdf(tmp_path / "text.pdf", text="B plus tree is an index structure " * 4)
    text, used_ocr = pdf_ocr.extract_page_text(p, 1)
    assert "index structure" in text
    assert used_ocr is False


def test_extract_page_text_ocr_fallback(tmp_path, monkeypatch):
    p = _make_pdf(tmp_path / "scan.pdf", with_image=True)
    monkeypatch.setattr(pdf_ocr, "ocr_pdf_pages", lambda *a, **k: {1: "OCR 结果"})
    text, used_ocr = pdf_ocr.extract_page_text(p, 1)
    assert text == "OCR 结果"
    assert used_ocr is True


def test_page_count(tmp_path):
    p = _make_pdf(tmp_path / "one.pdf", text="x")
    assert pdf_ocr.page_count(p) == 1


def test_ocr_pdf_pages_parallel(tmp_path, monkeypatch):
    import shutil

    import fitz

    p = tmp_path / "parallel.pdf"
    doc = fitz.open()
    for i in range(4):
        doc.new_page(width=200 + i * 10, height=200)
    doc.save(str(p))
    doc.close()

    monkeypatch.setenv("IMPORT_OCR_WORKERS", "2")
    monkeypatch.setattr(pdf_ocr, "select_engine", lambda prefer=None: ("rapidocr", ""))
    monkeypatch.setattr(pdf_ocr, "_ocr_image", lambda image, engine: "页文本")
    shutil.rmtree(pdf_ocr._cache_dir(p), ignore_errors=True)

    seen = []
    out = pdf_ocr.ocr_pdf_pages(
        p, on_page=lambda done, text, total: seen.append(done))

    assert list(out.keys()) == [1, 2, 3, 4]
    assert all(v == "页文本" for v in out.values())
    assert seen == [1, 2, 3, 4]
    assert pdf_ocr._ocr_workers() == 2


def test_ocr_workers_is_clamped(monkeypatch):
    monkeypatch.setenv("IMPORT_OCR_WORKERS", "99")
    assert pdf_ocr._ocr_workers() == 3
    monkeypatch.setenv("IMPORT_OCR_WORKERS", "0")
    assert pdf_ocr._ocr_workers() == 1


def _box(x0, x1, y):
    return [(x0, y), (x1, y), (x1, y + 30), (x0, y + 30)]


def test_reorder_two_columns_left_then_right():
    items = []
    for i, y in enumerate((100, 200, 300)):
        items.append((f"左{i+1}", _box(60, 180, y)))
        items.append((f"右{i+1}", _box(520, 640, y)))
    # OCR 原始顺序是逐行交错
    interleaved = [items[i] for i in (0, 1, 2, 3, 4, 5)]
    out = pdf_ocr._reorder_reading_order(interleaved, 1000)
    assert out == ["左1", "左2", "左3", "右1", "右2", "右3"]


def test_reorder_keeps_single_column_untouched():
    items = [(f"第{i+1}行", _box(60, 900, 100 + i * 100)) for i in range(4)]
    out = pdf_ocr._reorder_reading_order(items, 1000)
    assert out == ["第1行", "第2行", "第3行", "第4行"]


def test_reorder_full_width_title_stays_first():
    title = ("大标题", _box(60, 940, 40))
    left = [(f"左{i+1}", _box(60, 180, 200 + i * 100)) for i in range(2)]
    right = [(f"右{i+1}", _box(520, 640, 200 + i * 100)) for i in range(2)]
    out = pdf_ocr._reorder_reading_order([title] + left + right, 1000)
    assert out[0] == "大标题"
    assert out[1:3] == ["左1", "左2"]
    assert out[3:] == ["右1", "右2"]


def test_page_stats_distinguish_blank_and_failure(tmp_path, monkeypatch):
    """空白页（OCR 成功但无文字）与识别失败（抛异常）必须分开记录。"""
    import shutil

    import fitz

    p = tmp_path / "stats.pdf"
    doc = fitz.open()
    doc.new_page(width=200, height=200)   # 空页 → blank
    doc.new_page(width=200, height=200)   # 这页让 OCR 抛错 → failed
    doc.save(str(p))
    doc.close()

    monkeypatch.setattr(pdf_ocr, "select_engine", lambda prefer=None: ("rapidocr", ""))
    shutil.rmtree(pdf_ocr._cache_dir(p), ignore_errors=True)

    calls = {"n": 0}

    def _boom(image, engine):
        calls["n"] += 1
        if calls["n"] == 2:            # 第 2 页模拟崩溃
            raise RuntimeError("模拟 OCR 崩溃")
        return ""                      # 第 1 页成功但无文字 → blank

    monkeypatch.setattr(pdf_ocr, "_ocr_image", _boom)

    stats = {}
    out = pdf_ocr.ocr_pdf_pages(p, stats=stats)
    # 两页都返回空文本，但分类不同
    assert all(not (v or "").strip() for v in out.values())
    assert stats.get("failed"), f"失败页未记录: {stats}"
    assert not stats.get("blank") or set(stats["blank"]).isdisjoint(set(stats["failed"]))
