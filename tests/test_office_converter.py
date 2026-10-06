"""旧版 Office（.ppt/.doc）转换测试

有转换组件（LibreOffice 或 Microsoft Office COM）时走**真实转换**；
不可用时断言"明确失败、不假成功"。
"""

import shutil
from pathlib import Path

import pytest

from services import office_converter

LEGACY_SAMPLE = Path(r"D:\神の文档\langchain-rag-work\legacy_samples\旧课件.ppt")
FACT = "LEGACY-9241"


@pytest.fixture(autouse=True)
def _fresh_cache():
    office_converter.reset_cache()
    yield
    office_converter.reset_cache()


def test_capabilities_shape_and_truthful_kind():
    caps = office_converter.capabilities()
    assert set(caps) == {"available", "path", "kind", "note"}
    assert caps["kind"] in ("libreoffice", "msoffice", None)
    if caps["available"]:
        assert caps["path"] and caps["note"]
        assert caps["kind"] in ("libreoffice", "msoffice")
        assert office_converter.is_available() is True
        assert office_converter.find_converter()


def test_mode_none_disables_converter(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFICE_CONVERTER", "none")
    office_converter.reset_cache()
    caps = office_converter.capabilities()
    assert caps["available"] is False
    assert caps["kind"] is None
    assert office_converter.find_converter() is None
    assert office_converter.can_convert(".ppt") is False

    fake = tmp_path / "x.ppt"
    fake.write_bytes(b"not really a ppt")
    with pytest.raises(office_converter.OfficeConverterError) as ei:
        office_converter.convert(str(fake))
    assert ei.value.error_type == "converter_unavailable"


def test_mode_libreoffice_forced(monkeypatch):
    monkeypatch.setenv("OFFICE_CONVERTER", "libreoffice")
    monkeypatch.setenv("OFFICE_CONVERTER_PATH", r"C:\definitely\missing\soffice.exe")
    office_converter.reset_cache()
    caps = office_converter.capabilities()
    if caps["available"]:
        pytest.skip("本机确实安装了 LibreOffice，无法验证强制不可用分支")
    assert caps["kind"] is None
    assert "LibreOffice" in caps["note"]


def test_loader_reports_unavailable_without_pretending(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFICE_CONVERTER", "none")
    office_converter.reset_cache()
    sample = tmp_path / "x.ppt"
    sample.write_bytes(b"not really a ppt")

    from rag.loaders.legacy_office_loader import LegacyOfficeLoader
    result = LegacyOfficeLoader(enrich_images=False).load(str(sample))
    assert result.success is False
    assert result.error_type == "converter_unavailable"
    assert result.error_message


def _ensure_legacy_ppt(tmp_path: Path) -> Path:
    """优先用真实旧 PPT 样本；缺失时用 COM 从 pptx 真实转出 .ppt（不改后缀冒充）。"""
    if LEGACY_SAMPLE.exists():
        return LEGACY_SAMPLE
    from pptx import Presentation
    from pptx.util import Inches

    src = tmp_path / "source.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(8), Inches(1)) \
        .text_frame.text = "旧格式转换验证"
    slide.shapes.add_textbox(Inches(0.5), Inches(1.8), Inches(8), Inches(2)) \
        .text_frame.text = f"本页专用常数 {FACT}"
    prs.save(str(src))

    converted = office_converter.convert(str(src), target_ext=".ppt")
    legacy = tmp_path / "旧课件.ppt"
    shutil.copy2(converted["converted_path"], str(legacy))
    office_converter.cleanup(converted["output_dir"])
    assert legacy.stat().st_size > 0
    return legacy


@pytest.mark.slow
def test_real_legacy_ppt_conversion_extracts_fact(tmp_path):
    office_converter.reset_cache()
    caps = office_converter.capabilities()
    if not caps["available"]:
        pytest.skip(f"无 Office 转换组件：{caps['note']}")

    legacy = _ensure_legacy_ppt(tmp_path)

    from rag.loaders.legacy_office_loader import LegacyOfficeLoader
    result = LegacyOfficeLoader(enrich_images=False).load(str(legacy))
    assert result.success, result.error_message
    blob = "\n".join(getattr(d, "page_content", "") for d in result.documents)
    assert FACT in blob, "转换后的内容必须可被真实解析"

    conv = (result.metadata or {}).get("conversion") or {}
    assert conv.get("original_ext") == ".ppt"
    assert conv.get("converted_ext") == ".pptx"
    assert conv.get("converter_kind") in ("libreoffice", "msoffice")
    assert conv.get("page_mapping")
    # 引用要回溯到原始课件名
    assert result.documents
    meta = result.documents[0].metadata
    assert meta.get("file_name") == legacy.name
    assert meta.get("converted_from")
    assert str(meta.get("page")) == "1"


@pytest.mark.slow
def test_real_convert_pptx_to_ppt_and_back_is_real_file(tmp_path):
    """反向转换（.pptx -> .ppt）产物必须是真实旧格式，而非改后缀。"""
    office_converter.reset_cache()
    caps = office_converter.capabilities()
    if not caps["available"] or not office_converter.can_convert(".ppt"):
        pytest.skip(f"无 Office/COM 转换组件：{caps['note']}")

    from pptx import Presentation
    from pptx.util import Inches

    src = tmp_path / "source.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(8), Inches(1)) \
        .text_frame.text = "反向转换验证"
    prs.save(str(src))

    converted = office_converter.convert(str(src), target_ext=".ppt")
    try:
        out = Path(converted["converted_path"])
        assert out.suffix == ".ppt"
        head = out.read_bytes()[:8]
        # OLE2 复合文档头：真实旧 .ppt 的最低限度证据（pptx 是 ZIP，头为 PK）
        assert head.startswith(b"\xd0\xcf\x11\xe0"), head
    finally:
        office_converter.cleanup(converted["output_dir"])
