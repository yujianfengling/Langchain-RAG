"""
PPTX 真实解析测试（2026-10-07）

用 python-pptx 生成含标题/正文/表格/备注/组合形状/空白页/图片的多页 pptx，
断言：幻灯片顺序、表格 Markdown、备注、组合形状递归、空白页如实标记、图片文档。
"""

import os

from pptx import Presentation

from rag.loaders.pptx_loader import PptxLoader
from tests.import_samples import make_pptx


def _load(tmp_path, with_image=True):
    path = make_pptx(str(tmp_path / "课件.pptx"), with_image=with_image)
    loader = PptxLoader(enrich_images=False,
                        image_output_dir=str(tmp_path / "imgs"))
    return loader.load(path), path


def _slides(result):
    return [d for d in result.documents if d.metadata.get("modality") == "slide"]


def test_slide_order_title_and_body(tmp_path):
    result, _ = _load(tmp_path)
    assert result.success, result.error_message
    slides = _slides(result)
    assert [d.metadata["page"] for d in slides] == [1, 2, 3]
    assert slides[0].metadata["slide_title"] == "第一页标题"
    assert slides[0].metadata["file_name"] == "课件.pptx"
    assert slides[0].metadata["modality"] == "slide"

    body = slides[0].page_content
    assert "第一页标题" in body
    assert "第一页正文内容" in body
    # 组合形状（group shape）递归抽取
    assert "组合形状内文本" in body
    # 标题不应在正文里重复一遍
    assert body.count("第一页标题") == 1


def test_table_markdown_and_notes(tmp_path):
    result, _ = _load(tmp_path)
    slides = _slides(result)
    second = slides[1].page_content
    assert "| 名称 | 数值 |" in second
    assert "| alpha | 42 |" in second
    assert "重要备注" in second
    assert slides[1].metadata["slide_table_count"] == 1


def test_empty_slide_marked_honestly(tmp_path):
    result, _ = _load(tmp_path)
    slides = _slides(result)
    assert slides[2].metadata["slide_text_empty"] is True
    assert slides[2].page_content == ""
    assert result.metadata["empty_slides"] == [3]
    # 整个文件并非全部空白，因此仍算解析成功
    assert result.success


def test_images_become_image_documents(tmp_path):
    result, _ = _load(tmp_path)
    images = [d for d in result.documents if d.metadata.get("modality") == "image"]
    assert images, "含图片的 pptx 应产出 image 文档"
    for doc in images:
        assert doc.metadata["page"] == 1
        assert doc.metadata["file_name"] == "课件.pptx"
        assert doc.metadata["origin_file_type"] == "pptx"
        assert os.path.exists(doc.metadata["image_path"])


def test_corrupt_pptx_fails_with_type(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"this is not a pptx")
    result = PptxLoader(enrich_images=False).load(str(bad))
    assert result.success is False
    assert result.error_type == "parse_error"
    assert result.error_message


def test_fully_empty_deck_is_error(tmp_path):
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])  # 空白版式
    path = tmp_path / "empty.pptx"
    prs.save(str(path))
    result = PptxLoader(enrich_images=False).load(str(path))
    assert result.success is False
    assert result.error_type == "empty_content"


def test_unsupported_extension_rejected(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text("hi", encoding="utf-8")
    result = PptxLoader(enrich_images=False).load(str(path))
    assert result.success is False
    assert result.error_type == "unsupported_format"
