"""统一上传限制测试（rag/formats.py 单一来源）

覆盖任务要求：21MB 文档可过、超过普通文档/压缩包上限、解压后单文件与展开总量超限、
以及路由与服务层都从同一份配置取值（带准确原因，不假成功）。
"""

import zipfile

import pytest

from rag import formats
from services import archive_service as A
from services import import_service, knowledge_service


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    # 不真正跑后台解析/建库，只验证"提交时的限制判定"
    monkeypatch.setattr(import_service, "_run_job", lambda job_id: None)
    return tmp_path


def _clear_limit_env(monkeypatch):
    for name in ("IMPORT_MAX_DOCUMENT_MB", "IMPORT_MAX_FILE_MB",
                 "IMPORT_MAX_ARCHIVE_MB", "IMPORT_MAX_EXTRACT_FILE_MB",
                 "IMPORT_MAX_EXTRACT_TOTAL_MB", "IMPORT_MAX_TOTAL_MB",
                 "IMPORT_MAX_FILES", "IMPORT_MAX_DEPTH", "IMPORT_MAX_SECONDS"):
        monkeypatch.delenv(name, raising=False)


# ==================== 配置单一来源 ====================

def test_default_limit_values(monkeypatch):
    _clear_limit_env(monkeypatch)
    caps = formats.limits()
    assert caps["max_file_mb"] == 200          # 普通文档
    assert caps["max_archive_mb"] == 500       # 压缩包
    assert caps["max_extract_file_mb"] == 200  # 展开后单成员
    assert caps["max_extract_total_mb"] == 1024  # 单批展开总量
    assert caps["max_total_mb"] == 1024        # 旧字段兼容
    assert caps["max_files"] == 100
    assert caps["max_depth"] == 2
    # 同上：单批预算默认 900s（实测依据见 docs/IMPORT_TIMEOUT_RCA.md）
    assert caps["max_seconds"] == 900


def test_legacy_env_import_max_file_mb_still_honored(monkeypatch):
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("IMPORT_MAX_FILE_MB", "13")
    assert formats.limits()["max_file_mb"] == 13
    # 新名优先于旧名
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "21")
    assert formats.limits()["max_file_mb"] == 21


# ==================== 服务层：单文件上限按类型 ====================

def test_21mb_document_accepted(isolated):
    content = b"a" * (21 * 1024 * 1024)
    result = import_service.create_import_job("学科大", [("大文档.txt", content)], "u1")
    assert result["accepted"] == 1
    assert result["rejected"] == []


def test_over_document_limit_rejected_with_exact_reason(isolated, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "1")
    content = b"a" * (2 * 1024 * 1024)
    result = import_service.create_import_job("学科大", [("大文档.txt", content)], "u1")
    assert result["accepted"] == 0
    rejected = result["rejected"][0]
    assert rejected["error_type"] == "size_exceeded"
    assert "2.0" in rejected["reason"], rejected["reason"]
    assert "1MB" in rejected["reason"], rejected["reason"]
    assert "普通文档" in rejected["reason"]


def test_archive_uses_archive_limit_not_document_limit(isolated, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "1")
    monkeypatch.setenv("IMPORT_MAX_ARCHIVE_MB", "3")
    # 2MB 的 .zip 超过普通文档上限但未超过压缩包上限 -> 接受
    ok = import_service.create_import_job("学科A", [("包.zip", b"z" * (2 * 1024 * 1024))], "u1")
    assert ok["accepted"] == 1, ok
    # 4MB 的 .zip 超过压缩包上限 -> 拒绝且说明是压缩包
    bad = import_service.create_import_job("学科A", [("包.zip", b"z" * (4 * 1024 * 1024))], "u1")
    assert bad["accepted"] == 0
    assert "压缩包" in bad["rejected"][0]["reason"]
    assert bad["rejected"][0]["error_type"] == "size_exceeded"


def test_validate_file_size_reads_config(monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "1")
    monkeypatch.setenv("IMPORT_MAX_ARCHIVE_MB", "5")
    ok, _ = knowledge_service.validate_file_size(b"x", "小.txt")
    assert ok
    bad, msg = knowledge_service.validate_file_size(b"x" * (2 * 1024 * 1024), "大.txt")
    assert not bad
    assert "普通文档" in msg and "2.0" in msg
    # 同一个 2MB 作为压缩包在 5MB 上限内 -> 通过
    ok_zip, _ = knowledge_service.validate_file_size(b"x" * (2 * 1024 * 1024), "包.zip")
    assert ok_zip


def test_route_max_file_bytes_by_type(monkeypatch):
    from api.routes import knowledge
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "9")
    monkeypatch.setenv("IMPORT_MAX_ARCHIVE_MB", "13")
    assert knowledge._max_file_bytes("a.txt") == 9 * 1024 * 1024
    assert knowledge._max_file_bytes("a.zip") == 13 * 1024 * 1024
    assert knowledge._max_file_bytes() == 9 * 1024 * 1024


# ==================== 展开限制 ====================

def _make_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return path


def test_extract_member_over_limit_rejected(tmp_path):
    archive = _make_zip(tmp_path / "p.zip", [("big.bin", b"x" * (2 * 1024 * 1024))])
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(archive), str(tmp_path / "out"), limits={
            "max_total_bytes": 10 ** 9,
            "max_extract_file_bytes": 1 * 1024 * 1024,
            "max_files": 100, "max_depth": 2, "max_seconds": 30})
    assert ei.value.error_type == "archive_limit_exceeded"
    assert "单个文件" in str(ei.value)
    assert "2.0" in str(ei.value)


def test_extract_total_over_limit_rejected(tmp_path):
    archive = _make_zip(tmp_path / "p.zip",
                        [("a.bin", b"x" * (1024 * 1024)), ("b.bin", b"y" * (1024 * 1024))])
    with pytest.raises(A.ArchiveError) as ei:
        A.extract_archive(str(archive), str(tmp_path / "out"), limits={
            "max_total_bytes": 1 * 1024 * 1024,  # 1MB 总量，两个 1MB 成员
            "max_extract_file_bytes": 10 ** 9,
            "max_files": 100, "max_depth": 2, "max_seconds": 30})
    assert ei.value.error_type == "archive_limit_exceeded"
    assert "总量" in str(ei.value)
