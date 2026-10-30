"""页面级索引回归测试（2026-10-24）

用一个带文本层的临时 PDF 验证：
- 逐页渲染与入库；
- 按页关键词检索、页码/片段/页面图 URL；
- 重复构建跳过、force 重建；
- 工具调用能把页面结果登记为引用。
"""

import uuid
from pathlib import Path

from config.path_config import get_subjects_dir
from services import page_index, tutor_tools


def _make_pdf(subject_id: str) -> str:
    import fitz

    subject_dir = Path(get_subjects_dir()) / subject_id
    subject_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = subject_dir / "visual-test.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "UNIQUE_PAGE_PHRASE bending stress figure three")
    page.insert_text((72, 96), "This page exists only for the page index test.")
    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path)


def test_build_search_and_image():
    subject = "pg_" + uuid.uuid4().hex[:10]
    _make_pdf(subject)

    built = page_index.build_page_index(subject)
    assert built["ok"] is True
    assert built["documents"] == 1
    assert built["pages"] == 1

    status = page_index.get_page_index_status(subject)
    assert status["pages"] == 1
    assert status["documents"] == 1

    hits = page_index.search_pages(subject, "bending stress", top_k=3)
    assert len(hits) == 1
    assert hits[0]["page"] == 1
    assert "bending stress" in hits[0]["snippet"].lower()
    assert hits[0]["page_image_url"].startswith("/api/knowledge/")
    assert hits[0]["bbox"] and len(hits[0]["bbox"]) == 4
    assert 0 <= hits[0]["bbox"][0] < hits[0]["image_width"]
    assert 0 <= hits[0]["bbox"][1] < hits[0]["image_height"]
    assert "bending stress" in hits[0]["block_text"].lower()

    listing = page_index.list_document_pages(subject, hits[0]["doc_id"])
    assert listing["pages"][0]["page"] == 1
    assert listing["pages"][0]["page_image_url"].startswith("/api/knowledge/")

    image = page_index.get_page_image(subject, hits[0]["doc_id"], 1)
    assert image and Path(image).is_file()
    assert Path(image).is_relative_to(page_index.images_root())

    # 重复构建：断点跳过已完成页（2026-10-30 语义：按页跳过，不再有
    # "页数未变整份文档跳过"的旧计数，旧键 skipped_documents 已移除）
    again = page_index.build_page_index(subject)
    assert again["pages"] == 0
    assert again["skipped"] == 1

    # force 重建
    forced = page_index.build_page_index(subject, force=True)
    assert forced["pages"] == 1


def test_pptx_is_converted_before_indexing(monkeypatch, tmp_path):
    import fitz

    subject = "pg_" + uuid.uuid4().hex[:10]
    subject_dir = Path(get_subjects_dir()) / subject
    subject_dir.mkdir(parents=True, exist_ok=True)
    (subject_dir / "slides.pptx").write_bytes(b"fake-pptx")

    pdf_path = tmp_path / "slides.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PPTX_CONVERTED_PAGE binary tree")
    doc.save(str(pdf_path))
    doc.close()

    monkeypatch.setattr("services.office_converter.convert",
                        lambda src, target: {"converted_path": str(pdf_path)})
    out = page_index.build_page_index(subject)
    assert out["documents"] == 1
    assert out["pages"] == 1
    hits = page_index.search_pages(subject, "binary tree")
    assert hits and hits[0]["file_name"] == "slides.pptx"


def test_search_empty_and_unknown_subject():
    assert page_index.search_pages("no_such_subject", "anything") == []
    subject = "pg_" + uuid.uuid4().hex[:10]
    assert page_index.search_pages(subject, "") == []


def test_tool_registers_page_sources():
    subject = "pg_" + uuid.uuid4().hex[:10]
    _make_pdf(subject)
    page_index.build_page_index(subject)

    ctx = tutor_tools.ToolContext(user_id="u1", subject_id=subject,
                                  ability="chat", scope={"mode": "all"})
    out = tutor_tools.execute_tool("search_pages", {"query": "bending stress"}, ctx)
    assert out["ok"] is True
    assert out["page_count"] == 1
    assert out["sources"][0]["page"] == 1
    assert out["sources"][0]["page_image_url"]
