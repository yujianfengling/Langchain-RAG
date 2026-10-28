"""章节映射与页码展示的单元测试（2026-10-28）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rag.loaders import pdf_map


class _El:
    def __init__(self, page: int, content: str, font: float = 10.0, bold: bool = False):
        self.page = page
        self.content = content
        self.metadata = {"font_size": font, "is_bold": bold}


class _Doc:
    def __init__(self, page: int):
        self.metadata = {"page": page}


def _make_toc_pdf(path: Path) -> Path:
    import fitz

    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=300, height=200)
    doc.set_toc([[1, "第一章 绪论", 1], [2, "1.1 数据库系统", 2], [1, "第二章 关系模型", 3]])
    doc.save(str(path))
    doc.close()
    return path


def test_toc_headings(tmp_path):
    p = _make_toc_pdf(tmp_path / "toc.pdf")
    headings = pdf_map.toc_headings(p)
    assert [(h.page, h.level, h.title) for h in headings] == [
        (0, 1, "第一章 绪论"),
        (1, 2, "1.1 数据库系统"),
        (2, 1, "第二章 关系模型"),
    ]


def test_detect_text_heading():
    assert pdf_map.detect_text_heading("第一章 绪论") == (1, "第一章 绪论")
    # 中文章标题后常无空格（OCR 常见），也必须识别
    assert pdf_map.detect_text_heading("第一章数据库系统概述") == (1, "第一章数据库系统概述")
    assert pdf_map.detect_text_heading("2.3 关系代数") == (2, "2.3 关系代数")
    assert pdf_map.detect_text_heading("Chapter 4 Storage") == (1, "Chapter 4 Storage")
    assert pdf_map.detect_text_heading("这是一段普通正文，不是标题。") is None
    assert pdf_map.detect_text_heading("2026 年春季学期") is None
    assert pdf_map.detect_text_heading("x" * 60) is None


def test_collect_from_elements_font_and_regex():
    elements = [
        _El(0, "数据库系统概论", font=24),
        _El(0, "这是正文内容", font=10),
        _El(1, "第二章 关系模型", font=11),
        _El(1, "正文", font=10),
    ]
    headings = pdf_map.collect_from_elements(elements)
    titles = {h.title for h in headings}
    assert "数据库系统概论" in titles
    assert "第二章 关系模型" in titles
    assert "这是正文内容" not in titles


def test_merge_prefers_toc():
    toc = [pdf_map.Heading(0, 1, "第一章", "toc")]
    other = [pdf_map.Heading(0, 1, "封面", "text")]
    merged = pdf_map.merge_headings(toc, other)
    assert [h.title for h in merged] == ["第一章"]


def test_annotate_sets_section_and_parents():
    docs = [_Doc(0), _Doc(1), _Doc(2), _Doc(3)]
    headings = [
        pdf_map.Heading(0, 1, "第一章 绪论", "toc"),
        pdf_map.Heading(1, 2, "1.1 数据库系统", "toc"),
        pdf_map.Heading(2, 1, "第二章 关系模型", "toc"),
    ]
    pdf_map.annotate(docs, headings)
    assert docs[0].metadata["section"] == "第一章 绪论"
    assert docs[1].metadata["section"] == "1.1 数据库系统"
    assert docs[1].metadata["parent_headings"] == "第一章 绪论"
    assert docs[2].metadata["section"] == "第二章 关系模型"
    assert docs[3].metadata["section"] == "第二章 关系模型"


def test_annotate_keeps_existing_section_but_adds_path():
    doc = _Doc(1)
    doc.metadata["section"] = "已有章节"
    pdf_map.annotate([doc], [
        pdf_map.Heading(0, 1, "第一章", "toc"),
        pdf_map.Heading(1, 2, "1.1 小节", "toc"),
    ])
    assert doc.metadata["section"] == "已有章节"
    assert doc.metadata["parent_headings"] == "第一章"


def test_headings_from_pages():
    pages = {1: "第一章 绪论\n正文", 2: "1.1 数据库系统\n正文", 3: "普通正文"}
    headings = pdf_map.headings_from_pages(pages)
    assert [(h.page, h.title) for h in headings] == [
        (0, "第一章 绪论"),
        (1, "1.1 数据库系统"),
    ]


def test_display_page():
    # 只有标记了 page_base=0 的 PDF 文档才 +1
    assert pdf_map.display_page({"page": 0, "page_base": 0}) == 1
    assert pdf_map.display_page({"page": 5, "page_base": 0}) == 6
    assert pdf_map.display_page({"page": "3-5", "page_base": 0}) == "4-6"
    assert pdf_map.display_page({"page": "12", "page_base": 0}) == 13
    # 旧索引/普通文本没有 page_base：保持原值，不能盲加
    assert pdf_map.display_page({"page": 3}) == 3
    assert pdf_map.display_page({"page": 3, "page_base": 1}) == 3
    assert pdf_map.display_page({"page": None}) is None
    assert pdf_map.display_page({}) is None
    assert pdf_map.display_page(None) is None


def test_bare_number_is_not_heading():
    """表格里的 95、学号 2021001 这类纯数字行不能被当成章节标题。"""
    assert pdf_map.detect_text_heading("95") is None
    assert pdf_map.detect_text_heading("2021001 张三") is None
    assert pdf_map.detect_text_heading("76") is None


def test_numbered_heading_needs_text_after():
    assert pdf_map.detect_text_heading("3.1 关系模型") == (2, "3.1 关系模型")
    assert pdf_map.detect_text_heading("2 绪论") == (1, "2 绪论")
