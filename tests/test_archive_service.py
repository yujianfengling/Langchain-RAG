"""
压缩包导入服务测试（2026-10-07）

覆盖：子目录、中文名、空格、同名不覆盖、忽略项、路径穿越、
损坏/加密明确失败、限额、7z、嵌套层级、rar 外部工具如实报告、
Office 容器不被当普通压缩包。
"""

import pytest

from services import archive_service as A
from tests.import_samples import (
    force_zip_encrypted_flag,
    make_7z,
    make_encrypted_7z,
    make_zip,
    make_zip_with_gbk_name,
)


def test_zip_subdirs_chinese_dupes_and_ignored(tmp_path):
    archive = tmp_path / "资料包.zip"
    make_zip(str(archive), [
        ("第1章/课件.pptx", b"AAA"),
        ("第2章/课件.pptx", b"BBB"),
        ("第1章/说明 空格.txt", b"hello"),
        ("__MACOSX/._x", b"junk"),
        (".hidden", b"x"),
        ("Thumbs.db", b"x"),
        ("desktop.ini", b"x"),
        ("~$temp.docx", b"x"),
        ("a.exe", b"MZ"),
        ("../evil.txt", b"evil"),
        ("/abs.txt", b"abs"),
    ])
    result = A.extract_archive(str(archive), str(tmp_path / "out"))

    rels = {e["source_path"] for e in result["entries"]}
    assert rels == {"第1章/课件.pptx", "第2章/课件.pptx", "第1章/说明 空格.txt"}

    display = [e["display_name"] for e in result["entries"]]
    assert len(display) == len(set(display)), "同名文件不得互相覆盖"
    assert "第1章__课件.pptx" in display
    assert "第2章__课件.pptx" in display

    # 两个同名课件内容互不相同，且各自落盘
    by_display = {e["display_name"]: open(e["abs_path"], "rb").read()
                  for e in result["entries"]}
    assert by_display["第1章__课件.pptx"] == b"AAA"
    assert by_display["第2章__课件.pptx"] == b"BBB"

    skipped = {s["source_path"]: s for s in result["skipped"]}
    assert skipped["../evil.txt"]["error_type"] == "archive_unsafe_path"
    assert skipped["/abs.txt"]["error_type"] == "archive_unsafe_path"
    assert any("__MACOSX" in p for p in skipped)
    assert any(p.endswith("Thumbs.db") for p in skipped)
    assert any(p.startswith("~$") for p in skipped)
    assert any(p == ".hidden" for p in skipped)
    assert any(p == "a.exe" for p in skipped)
    assert result["summary"]["skipped"] == len(result["skipped"])


def test_zip_gbk_filename_decoded(tmp_path):
    archive = tmp_path / "gbk.zip"
    make_zip_with_gbk_name(str(archive))
    result = A.extract_archive(str(archive), str(tmp_path / "out"))
    assert [e["source_path"] for e in result["entries"]] == ["中文名.txt"]
    assert open(result["entries"][0]["abs_path"], "rb").read().decode("gbk") == "中文内容"


def test_corrupt_zip_fails(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"definitely not a zip")
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(bad), str(tmp_path / "out"))
    assert ei.value.error_type == "archive_corrupt"


def test_encrypted_zip_fails_not_success(tmp_path):
    archive = tmp_path / "enc.zip"
    make_zip(str(archive), [("secret.txt", b"secret")])
    force_zip_encrypted_flag(str(archive))
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(archive), str(tmp_path / "out"))
    assert ei.value.error_type == "archive_encrypted"


def test_encrypted_7z_fails_not_success(tmp_path):
    pytest.importorskip("py7zr")
    archive = tmp_path / "enc.7z"
    make_encrypted_7z(str(archive))
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(archive), str(tmp_path / "out"))
    assert ei.value.error_type == "archive_encrypted"


def test_limits_rejected(tmp_path):
    archive = tmp_path / "many.zip"
    make_zip(str(archive), [(f"f{i}.txt", b"x") for i in range(5)])
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(archive), str(tmp_path / "out"), limits={
            "max_total_bytes": 10 ** 9, "max_files": 2,
            "max_depth": 1, "max_seconds": 30})
    assert ei.value.error_type == "archive_limit_exceeded"


def test_7z_extraction(tmp_path):
    pytest.importorskip("py7zr")
    archive = tmp_path / "包.7z"
    make_7z(str(archive), [("目录/文件.txt", "七z内容")])
    result = A.extract_archive(str(archive), str(tmp_path / "out"))
    assert [e["source_path"] for e in result["entries"]] == ["目录/文件.txt"]
    content = open(result["entries"][0]["abs_path"], encoding="utf-8").read()
    assert "七z内容" in content


def test_nested_archive_limited_depth(tmp_path):
    inner = tmp_path / "inner.zip"
    make_zip(str(inner), [("a.txt", b"inner-content")])
    outer = tmp_path / "outer.zip"
    make_zip(str(outer), [("sub/inner.zip", inner.read_bytes())])

    result = A.extract_archive(str(outer), str(tmp_path / "out"))
    rels = [e["source_path"] for e in result["entries"]]
    assert any(r.startswith("sub/inner.zip::") and r.endswith("a.txt") for r in rels)
    assert result["nested"], "应记录嵌套压缩包展开"

    # 层级限制为 0：嵌套包被跳过并给出 archive_nested_limit
    result2 = A.extract_archive(str(outer), str(tmp_path / "out2"), limits={
        "max_total_bytes": 10 ** 9, "max_files": 100,
        "max_depth": 0, "max_seconds": 30})
    assert not any(e["source_path"].endswith("a.txt") for e in result2["entries"])
    assert any(s["error_type"] == "archive_nested_limit" for s in result2["skipped"])


def test_rar_reports_honestly(tmp_path):
    from rag import formats
    spec = formats.get_format(".rar")
    if A.rar_tool() is None:
        assert spec.available is False
        assert spec.note
        fake = tmp_path / "x.rar"
        fake.write_bytes(b"rubbish")
        with pytest.raises(A.ArchiveError) as ei:
            A.extract_archive(str(fake), str(tmp_path / "out"))
        assert ei.value.error_type == "archive_unsupported"
    else:
        assert spec.available is True


def test_unsupported_archive_extension(tmp_path):
    fake = tmp_path / "x.tar"
    fake.write_bytes(b"x")
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(fake), str(tmp_path / "out"))
    assert ei.value.error_type == "archive_unsupported"


def test_office_containers_not_treated_as_archives():
    from rag import formats
    from rag.loaders import get_loader_for_file
    for ext in (".docx", ".pptx", ".xlsx"):
        assert formats.is_archive(ext) is False
        assert get_loader_for_file("x" + ext) is not None
