"""页面索引复用与失效回归测试（2026-10-30）

锁定本轮修复的行为契约：
- 有导入 OCR 缓存的页**绝不二次 OCR**（PageTextCache 严格命中）；
- 页级断点：删掉部分已建页后重建，只补缺失页；
- 删除资料后页面搜索/原图立即失效；取消建页任务后不再写库；
- m:/f: 身别别名解析正确。
"""

import json
import uuid
from pathlib import Path

from config.path_config import get_subjects_dir
from rag.loaders import pdf_ocr
from services import page_index


def _subject() -> str:
    return "pgt_" + uuid.uuid4().hex[:10]


def _make_scanned_pdf(subject_id: str, name: str = "scanned.pdf", pages: int = 2) -> Path:
    """造一个**无文本层**的 PDF：页面内容是光栅化的文字图。"""
    import fitz

    subject_dir = Path(get_subjects_dir()) / subject_id
    subject_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = subject_dir / name
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        # 往页面里插入图片（无文本层），OCR 才是取得文本的唯一途径
        img_doc = fitz.open()
        img_page = img_doc.new_page(width=500, height=100)
        img_page.insert_text((40, 60), f"CACHED_PAGE_PHRASE_{i} unique token")
        pix = img_page.get_pixmap(dpi=150)
        img_doc.close()
        page.insert_image(fitz.Rect(20, 100, 575, 190), pixmap=pix)
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def _make_text_pdf(subject_id: str, name: str = "text.pdf") -> Path:
    import fitz

    subject_dir = Path(get_subjects_dir()) / subject_id
    subject_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = subject_dir / name
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "TEXT_LAYER_PHRASE relational algebra")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def _prefill_ocr_cache(pdf_path: Path, texts: dict) -> None:
    """模拟导入期已生成的页级 OCR 缓存（严格走同一套命名规则）。"""
    engine, dpi = "rapidocr", pdf_ocr._DEFAULT_DPI
    for page_no, text in texts.items():
        cache = pdf_ocr._cache_path(pdf_path, page_no, engine, dpi)
        pdf_ocr._write_cache(cache, text)


def test_cached_pages_never_reocr(monkeypatch):
    """导入已有 OCR 缓存 → 建页只做渲染，OCR 一次都不跑。"""
    subject = _subject()
    pdf = _make_scanned_pdf(subject)
    _prefill_ocr_cache(pdf, {1: "缓存文本 alpha unique token", 2: "缓存文本 beta token"})

    calls = {"n": 0}

    def _fail_ocr(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("有缓存却调了 OCR —— 复用失效")

    monkeypatch.setattr(pdf_ocr, "ocr_pdf_pages", _fail_ocr)

    out = page_index.build_document_page_index(subject, pdf)
    assert out["ok"] is True, out
    assert out["reused_cache"] == 2
    assert out["ocr_pages"] == 0
    assert calls["n"] == 0

    # 文本确实来自缓存
    hits = page_index.search_pages(subject, "alpha unique")
    assert hits and "缓存文本" in hits[0]["snippet"]


def test_missing_cache_falls_back_to_real_ocr(monkeypatch):
    """没有缓存的页才允许 OCR（且结果会写缓存，供下次复用）。"""
    subject = _subject()
    pdf = _make_scanned_pdf(subject)

    def _fake_ocr(pdf_path, pages=None, **kwargs):
        return {p: f"OCR结果 page{p} gamma token" for p in (pages or range(1, 3))}

    monkeypatch.setattr(pdf_ocr, "ocr_pdf_pages", _fake_ocr)
    out = page_index.build_document_page_index(subject, pdf)
    assert out["ok"] is True
    assert out["ocr_pages"] == 2
    assert page_index.search_pages(subject, "gamma token")


def test_page_breakpoint_only_rebuilds_missing(monkeypatch):
    """删掉部分已建页 → 重建只补缺失页，已完成页不重写。"""
    subject = _subject()
    pdf = _make_text_pdf(subject)  # 单页即可
    _make_scanned_pdf(subject, "scan2.pdf", pages=3)
    _prefill_ocr_cache(
        Path(get_subjects_dir()) / subject / "scan2.pdf",
        {1: "断点页1 delta", 2: "断点页2 delta", 3: "断点页3 delta"})

    page_index.build_page_index(subject)
    status = page_index.get_page_index_status(subject)
    assert status["pages"] == 4

    # 模拟中断：删掉 scan2 的第 2、3 页（DB 行 + 页面图）
    conn = page_index._conn()
    try:
        rows = conn.execute(
            "SELECT page_no, image_path FROM pages WHERE subject_id=? AND doc_id=?",
            (subject, "f:scan2.pdf")).fetchall()
        assert len(rows) == 3
        for row in rows:
            if row["page_no"] >= 2:
                conn.execute(
                    "DELETE FROM pages WHERE subject_id=? AND doc_id=? AND page_no=?",
                    (subject, "f:scan2.pdf", row["page_no"]))
                Path(row["image_path"]).unlink(missing_ok=True)
        conn.commit()
    finally:
        conn.close()

    out = page_index.build_page_index(subject)
    assert out["pages"] == 2          # 只补了 2 页，不是 4 页
    assert out["skipped"] == 2
    hits = page_index.search_pages(subject, "断点页3")
    assert hits and hits[0]["page"] == 3


def test_delete_document_invalidates_pages():
    """删除资料 → 页面搜索/原图立即失效，不再指向已删资料。"""
    subject = _subject()
    pdf = _make_text_pdf(subject)
    page_index.build_page_index(subject)
    assert page_index.search_pages(subject, "relational algebra")

    removed = page_index.delete_document_pages(subject, file_name=pdf.name)
    assert removed["ok"] is True and removed["deleted"] == 1
    assert page_index.search_pages(subject, "relational algebra") == []
    assert page_index.get_page_image(subject, "f:" + pdf.name, 1) is None


def test_cancel_prevents_writeback():
    """先取消任务再建页 → 一页都不写（防"删完又被旧任务写回来"）。"""
    subject = _subject()
    pdf = _make_text_pdf(subject)
    page_index.cancel_document_tasks(subject, "f:" + pdf.name)
    out = page_index.build_document_page_index(subject, pdf)
    assert out["ok"] is True
    assert out["indexed"] == 0
    assert out["cancelled"] is True
    assert page_index.search_pages(subject, "relational algebra") == []


def test_material_alias_resolution():
    """m:<material_id> 别名 → 同一份页面的两种身份都能取。"""
    subject = _subject()
    pdf = _make_text_pdf(subject)
    page_index.build_document_page_index(
        subject, pdf, material_id="mat_001")
    page_index.set_doc_alias(subject, "m:mat_001", "f:" + pdf.name)

    by_file = page_index.get_page_image(subject, "f:" + pdf.name, 1)
    by_material = page_index.get_page_image(subject, "m:mat_001", 1)
    assert by_file and by_material and by_file == by_material

    listing = page_index.list_document_pages(subject, "m:mat_001")
    assert listing["pages"] and listing["pages"][0]["page"] == 1


def test_no_bbox_is_not_faked():
    """无文本层（走缓存/OCR）的页不伪造坐标：blocks 为空、bbox 为 None。"""
    subject = _subject()
    pdf = _make_scanned_pdf(subject)
    _prefill_ocr_cache(pdf, {1: "无坐标页 epsilon token", 2: "无坐标页 zeta token"})

    page_index.build_document_page_index(subject, pdf)
    hits = page_index.search_pages(subject, "epsilon token")
    assert hits
    assert hits[0]["bbox"] is None, "缓存文本没有坐标，不得伪造整页框"

    conn = page_index._conn()
    try:
        row = conn.execute(
            "SELECT blocks_json FROM pages WHERE subject_id=? AND page_no=1",
            (subject,)).fetchone()
        assert json.loads(row["blocks_json"] or "[]") == []
    finally:
        conn.close()


def test_truncation_is_explicit():
    """超页数上限必须如实返回未覆盖范围，不静默截断。"""
    subject = _subject()
    monkey = None
    # 用 monkeypatch 的最小替代：直接改常量
    original = page_index._MAX_PAGES_PER_DOC
    page_index._MAX_PAGES_PER_DOC = 2
    try:
        pdf = _make_scanned_pdf(subject, "bigbook.pdf", pages=5)
        _prefill_ocr_cache(pdf, {p: f"截断页{i} theta token"
                                 for p, i in zip(range(1, 6), range(5))})
        out = page_index.build_document_page_index(subject, pdf)
        assert out["truncated"] is True
        assert out["total_pages"] == 5
        assert out["covered_to"] == 2
        assert out["uncovered_from"] == 3
        assert out["uncovered_pages"] == 3

        # 继续处理剩余页
        out2 = page_index.build_document_page_index(
            subject, pdf, page_from=out["uncovered_from"])
        assert out2["ok"] is True
        assert out2["indexed"] == 3
        hits = page_index.search_pages(subject, "theta token")
        assert len({h["page"] for h in hits}) >= 2
    finally:
        page_index._MAX_PAGES_PER_DOC = original
    assert monkey is None


def test_clear_cancel_request_allows_rebuild_after_replace():
    """替换失效记取消账后，新版调度前清账 → 重建正常写页（2026-10-31 回归）。

    实测 bug：同名替换导入时 _invalidate_replaced_pages 记取消账，
    同一次导入随后调度的重建任务注册时被旧账误杀 → pages=0。
    """
    subject = _subject()
    pdf = _make_text_pdf(subject)
    doc_id = "f:" + pdf.name

    # 模拟替换失效动作：删页 + 记取消账
    page_index.cancel_document_tasks(subject, doc_id)
    page_index.delete_document_pages(subject, file_name=pdf.name)

    # 旧任务此刻注册仍必须被拦（保护语义不回退）
    blocked = page_index.build_document_page_index(subject, pdf)
    assert blocked["cancelled"] is True and blocked["indexed"] == 0

    # 新版调度：先清账再建页（import_service._worker 的行为）
    page_index.clear_cancel_request(subject, doc_id)
    out = page_index.build_document_page_index(subject, pdf)
    assert out["ok"] is True and out["indexed"] == 1
    assert page_index.search_pages(subject, "relational algebra")

    # 清账后 doc_state 与 pages 一致（不再出现 0 页却显示已覆盖）
    status = page_index.get_page_index_status(subject)
    detail = [d for d in status["document_details"] if d["doc_id"] == doc_id]
    assert detail and detail[0]["indexed_pages"] == detail[0]["total_pages"]


def test_cancelled_build_reports_honest_coverage():
    """被取消的建页不得把未建页报成已覆盖。"""
    subject = _subject()
    pdf = _make_text_pdf(subject, "cancel_honest.pdf")
    doc_id = "f:" + pdf.name
    page_index.cancel_document_tasks(subject, doc_id)
    out = page_index.build_document_page_index(subject, pdf)
    assert out["cancelled"] is True
    status = page_index.get_page_index_status(subject)
    detail = [d for d in status["document_details"] if d["doc_id"] == doc_id]
    assert detail, "取消后也应留下如实的 doc_state 行"
    assert detail[0]["indexed_pages"] == 0
    assert detail[0]["covered_to"] == 0
    assert detail[0]["truncated"] is False


def test_clear_cancel_request_clears_subject_wildcard():
    """删除最后一份资料记学科级通配账 → 新导入的单文档清账必须连它一起清
    （否则清库后再上传，自动建页被通配账取消——2026-10-31 二次实测）。"""
    subject = _subject()
    pdf = _make_text_pdf(subject)
    # 模拟"清库"动作：学科级取消（不带 doc_id）
    page_index.cancel_document_tasks(subject)
    blocked = page_index.build_document_page_index(subject, pdf)
    assert blocked["cancelled"] is True and blocked["indexed"] == 0

    # 新导入调度：按 doc_id 清账
    page_index.clear_cancel_request(subject, "f:" + pdf.name)
    out = page_index.build_document_page_index(subject, pdf)
    assert out["ok"] is True and out["indexed"] == 1
    assert page_index.search_pages(subject, "relational algebra")
