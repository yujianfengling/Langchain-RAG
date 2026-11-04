"""流式上传的临时文件清理（2026-10-28）

覆盖三条容易漏删的路径：
1. 上传成功：临时文件被 move 进任务目录，incoming 目录不留残留；
2. 空文件/超限被拒：半成品文件必须删掉；
3. 客户端中途断连（读取时抛异常）：正在写的那个文件也要删掉。
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest import mock

import pytest

from api.routes.knowledge import import_materials
from services import import_service


class _ExplodingUpload:
    """模拟客户端中途断连：读到一半抛异常。"""

    def __init__(self, filename: str = "big.pdf", first: bytes = b"x" * 4096):
        self.filename = filename
        self._first = first
        self._done = False

    async def read(self, size: int = -1):
        if self._done:
            raise RuntimeError("client disconnected")
        self._done = True
        return self._first


def _upload(filename: str, payload: bytes):
    from fastapi import UploadFile

    return UploadFile(filename=filename, file=io.BytesIO(payload))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _incoming_dir() -> Path:
    return Path(import_service._import_tmp_root()) / "incoming"


def test_cleanup_stale_import_tmp_only_terminal_and_old(tmp_path, monkeypatch):
    import os
    import time

    root = tmp_path / "import_tmp"
    root.mkdir()
    old = root / "imp_old"
    fresh = root / "imp_fresh"
    old.mkdir()
    fresh.mkdir()
    os.utime(old, (time.time() - 48 * 3600, time.time() - 48 * 3600))
    monkeypatch.setattr(import_service, "_import_tmp_root", lambda: root)
    monkeypatch.setattr(import_service, "get_job",
                        lambda job_id: {"status": "succeeded" if job_id == "imp_old" else "running"})
    cleaned = []
    monkeypatch.setattr(import_service, "cleanup_job_files", lambda job_id: cleaned.append(job_id))

    assert import_service.cleanup_stale_import_tmp(max_age_hours=24) == 1
    assert cleaned == ["imp_old"]


def test_successful_upload_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(import_service, "create_import_job",
                        lambda sid, payload, uid: {"job_id": "j1", "accepted": 1,
                                                   "rejected": []})
    before = sorted(p.name for p in _incoming_dir().glob("*")) if _incoming_dir().exists() else []
    _run(import_materials(subject_id="subj_a", files=[_upload("a.txt", b"hello")],
                          user_id="u1"))
    after = sorted(p.name for p in _incoming_dir().glob("*")) if _incoming_dir().exists() else []
    assert after == before, f"上传成功后仍残留临时文件: {set(after) - set(before)}"


def test_empty_file_is_rejected_and_temp_removed(monkeypatch):
    monkeypatch.setattr(import_service, "create_import_job",
                        lambda sid, payload, uid: {"job_id": "j1", "accepted": 0,
                                                   "rejected": []})
    before = {p.name for p in _incoming_dir().glob("*")} if _incoming_dir().exists() else set()
    with pytest.raises(Exception):
        _run(import_materials(subject_id="subj_a", files=[_upload("empty.txt", b"")],
                              user_id="u1"))
    after = {p.name for p in _incoming_dir().glob("*")} if _incoming_dir().exists() else set()
    assert after == before, f"空文件被拒后仍残留: {after - before}"


def test_client_disconnect_removes_partial_file(monkeypatch):
    monkeypatch.setattr(import_service, "create_import_job",
                        lambda sid, payload, uid: {"job_id": "j1", "accepted": 0,
                                                   "rejected": []})
    before = {p.name for p in _incoming_dir().glob("*")} if _incoming_dir().exists() else set()
    with pytest.raises(Exception):
        _run(import_materials(subject_id="subj_a", files=[_ExplodingUpload()],
                              user_id="u1"))
    after = {p.name for p in _incoming_dir().glob("*")} if _incoming_dir().exists() else set()
    assert after == before, f"断连后仍残留半成品: {after - before}"


def test_oversize_upload_removes_partial_file(monkeypatch):
    """超限抛错时，已经写了几 MB 的半成品同样要删掉。"""
    monkeypatch.setenv("IMPORT_MAX_DOCUMENT_MB", "1")
    from rag import formats

    monkeypatch.setattr(formats, "upload_limit_bytes", lambda ext: 1024 * 1024)
    monkeypatch.setattr(import_service, "create_import_job",
                        lambda sid, payload, uid: {"job_id": "j1", "accepted": 0,
                                                   "rejected": []})
    before = {p.name for p in _incoming_dir().glob("*")} if _incoming_dir().exists() else set()
    with pytest.raises(Exception):
        _run(import_materials(subject_id="subj_a",
                              files=[_upload("big.bin", b"x" * (2 * 1024 * 1024))],
                              user_id="u1"))
    after = {p.name for p in _incoming_dir().glob("*")} if _incoming_dir().exists() else set()
    assert after == before, f"超限后仍残留: {after - before}"
