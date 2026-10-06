"""
统一格式能力清单（rag/formats.py）一致性测试（2026-10-07）

锁定"单一事实来源"：能力清单 / accept / 解析器分派必须与真实可用性一致，
不能出现"能选中后缀但解析器不认识"或"清单说支持但实际不可用"。
"""

import pytest

from rag import formats


PUBLIC_FIELDS = {"ext", "kind", "label", "parser", "requires", "available", "note"}


def test_capabilities_shape_follows_contract():
    caps = formats.capabilities()
    assert set(caps) >= {"formats", "archives", "converter", "limits", "accept"}
    assert caps["formats"], "能力清单不能为空"
    for item in caps["formats"] + caps["archives"]:
        assert set(item) == PUBLIC_FIELDS, item
        assert not item["ext"].startswith("."), item
        assert item["kind"]
        assert item["label"]
        assert item["parser"]
        assert isinstance(item["available"], bool)


def test_accept_only_contains_available():
    caps = formats.capabilities()
    assert caps["accept"]
    accepted = {formats.normalize_ext(x) for x in caps["accept"].split(",")}
    all_specs = formats.list_formats() + formats.list_archives()
    available = {s.ext for s in all_specs if s.available}
    assert accepted == available, (accepted ^ available)


def test_available_matches_actual_modules():
    cases = [(".pptx", "pptx"), (".xlsx", "openpyxl"), (".xls", "xlrd"),
             (".7z", "py7zr"), (".pdf", "pdfplumber")]
    for ext, module in cases:
        spec = formats.get_format(ext)
        assert spec is not None
        assert spec.available == formats.module_available(module), ext


def test_office_container_formats_are_not_archives():
    for ext in (".docx", ".pptx", ".xlsx"):
        spec = formats.get_format(ext)
        assert spec is not None
        assert spec.archive is False
        assert formats.is_archive(ext) is False


def test_ppt_and_doc_follow_converter_capability():
    conv = formats.converter_capabilities()
    assert set(conv) >= {"available", "path", "kind", "note"}
    # kind 必须如实反映实际后端（libreoffice / msoffice / None）
    assert conv["kind"] in ("libreoffice", "msoffice", None)
    for ext in (".ppt", ".doc"):
        spec = formats.get_format(ext)
        assert spec.requires in ("libreoffice", "msoffice")
        assert spec.available == conv["available"]
        if conv["available"]:
            assert spec.note and conv["path"]
        else:
            assert spec.note, "不可用时必须给出明确原因"
            assert "LibreOffice" in spec.note
            assert ext not in formats.accept_string().split(",")


def test_rar_requires_external_tool():
    from services import archive_service
    spec = formats.get_format(".rar")
    assert spec.requires == "unrar"
    if archive_service.rar_tool() is None:
        assert spec.available is False
        assert spec.note
    else:
        assert spec.available is True


def test_unavailable_reason_is_readable():
    assert "不支持" in formats.unavailable_reason(".xyz")
    from services import archive_service
    if archive_service.rar_tool() is None:
        assert "rar" in formats.unavailable_reason(".rar").lower()


def test_limits_read_from_env(monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_FILES", "7")
    monkeypatch.setenv("IMPORT_MAX_DEPTH", "3")
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "5")
    monkeypatch.setenv("IMPORT_MAX_ARCHIVE_MB", "9")
    monkeypatch.setenv("IMPORT_MAX_EXTRACT_FILE_MB", "4")
    monkeypatch.setenv("IMPORT_MAX_EXTRACT_TOTAL_MB", "33")
    monkeypatch.setenv("IMPORT_MAX_SECONDS", "12")
    caps = formats.limits()
    assert caps["max_files"] == 7
    assert caps["max_depth"] == 3
    assert caps["max_file_mb"] == 5
    assert caps["max_archive_mb"] == 9
    assert caps["max_extract_file_mb"] == 4
    assert caps["max_extract_total_mb"] == 33
    assert caps["max_total_mb"] == 33  # 旧字段语义 = 单批展开总量
    assert caps["max_seconds"] == 12


def test_limits_defaults(monkeypatch):
    for name in ("IMPORT_MAX_DOCUMENT_MB", "IMPORT_MAX_FILE_MB",
                 "IMPORT_MAX_ARCHIVE_MB", "IMPORT_MAX_EXTRACT_FILE_MB",
                 "IMPORT_MAX_EXTRACT_TOTAL_MB", "IMPORT_MAX_FILES",
                 "IMPORT_MAX_DEPTH", "IMPORT_MAX_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    caps = formats.limits()
    assert caps["max_file_mb"] == 200
    assert caps["max_archive_mb"] == 500
    assert caps["max_extract_file_mb"] == 200
    assert caps["max_extract_total_mb"] == 1024
    assert caps["max_total_mb"] == 1024
    assert caps["max_files"] == 100
    assert caps["max_depth"] == 2
    # 2026-10-08 语义变更：max_seconds 改为**整个任务**的墙钟上限，默认 7200s。
    # “单批预算到点就把剩余丢给用户”已改为后台自动分批（每批 IMPORT_BATCH_SECONDS，
    # 默认 240s，自动接续）；单文件/单张视觉超时另有独立上限。
    assert caps["max_seconds"] == 7200


def test_upload_limit_depends_on_archive(monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "7")
    monkeypatch.setenv("IMPORT_MAX_ARCHIVE_MB", "11")
    assert formats.upload_limit_mb(".txt") == 7
    assert formats.upload_limit_mb(".zip") == 11
    assert formats.upload_limit_mb(".rar") == 11
    assert formats.upload_limit_bytes(".txt") == 7 * 1024 * 1024
    assert formats.upload_limit_bytes(".7z") == 11 * 1024 * 1024


def test_requires_office_converter_helper():
    assert formats.requires_office_converter(".ppt") is True
    assert formats.requires_office_converter(".doc") is True
    assert formats.requires_office_converter(".pdf") is False
    assert formats.requires_office_converter(".zip") is False
    assert formats.requires_office_converter(".xyz") is False


def test_loader_dispatch_matches_parseable_extensions():
    from rag.loaders import get_loader_for_file
    for ext in formats.parseable_extensions():
        assert get_loader_for_file("sample" + ext) is not None, ext
    for ext in formats.archive_extensions():
        assert get_loader_for_file("sample" + ext) is None, ext


def test_parseable_extensions_exclude_archives():
    assert not (set(formats.parseable_extensions()) & set(formats.archive_extensions()))
