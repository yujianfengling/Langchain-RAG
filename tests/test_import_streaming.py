"""大文件流式落盘的单元测试（2026-10-28）。"""

from __future__ import annotations

import services.import_service as imp


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(imp, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(imp, "_run_job_thread", lambda *a, **k: None)


def test_create_job_accepts_path_and_moves_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4 test content")

    result = imp.create_import_job("stream_subject", [("a.pdf", src)], user_id="u")

    assert result["accepted"] == 1
    assert result["rejected"] == []
    assert not src.exists(), "被接受的文件应 move 到任务目录，而不是留在临时目录"
    job = imp.get_job(result["job_id"])
    assert job is not None
    assert job["totals"]["bytes"] == len(b"%PDF-1.4 test content")


def test_create_job_still_accepts_bytes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    result = imp.create_import_job("stream_subject", [("b.txt", b"hello")], user_id="u")
    assert result["accepted"] == 1

    job = imp.get_job(result["job_id"])
    assert job["totals"]["bytes"] == 5


def test_create_job_rejects_oversize_path(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(imp, "_limits", lambda: {
        "max_file_mb": 1, "max_archive_mb": 1, "max_extract_file_mb": 1,
        "max_extract_total_mb": 1, "max_total_mb": 1, "max_files": 10,
        "max_depth": 2, "max_seconds": 60,
    })
    src = tmp_path / "big.pdf"
    src.write_bytes(b"x" * (2 * 1024 * 1024))

    result = imp.create_import_job("stream_subject", [("big.pdf", src)], user_id="u")

    assert result["accepted"] == 0
    assert result["rejected"]
    assert result["rejected"][0]["error_type"] == "size_exceeded"
    assert "超过" in result["rejected"][0]["reason"]
