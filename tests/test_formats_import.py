"""
端到端验收回归测试（2026-10-07，导入契约）

锁定验收反馈中的两条：
1) 损坏/加密压缩包必须在 items 里有 failed 条目（带 error_type 与可读原因），
   archive_summaries 说明该包发生了什么；每条被接受的文件都有终态记录。
2) 一批资料全部失败时，**不得**破坏已有知识库（已有的仍可检索）。
"""

import time

import pytest

from tests.import_samples import (
    force_zip_encrypted_flag,
    make_txt,
    make_zip,
)


def _wait(svc, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = svc.get_job(job_id)
        if job and job["status"] in svc.TERMINAL_JOB_STATUSES:
            return job
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束: {svc.get_job(job_id)}")


@pytest.fixture
def isolated_import(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    from services import import_service, knowledge_service
    monkeypatch.setattr(knowledge_service,
                        "rebuild_subject_knowledge_from_documents",
                        lambda subject_id, documents: {"success": True,
                                                       "subject_id": subject_id})
    return import_service


def test_corrupt_zip_yields_failed_item_with_error_type(tmp_path, isolated_import):
    svc = isolated_import
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"definitely not a zip")

    result = svc.create_import_job("学科A", [("corrupt.zip", bad.read_bytes())], "u1")
    assert result["accepted"] == 1, "损坏压缩包应先被接受，再由后台如实失败"

    job = _wait(svc, result["job_id"])
    assert job["status"] == "failed"
    assert job["items"], "被接受的压缩包必须在 items 里有终态记录"
    item = job["items"][0]
    assert item["status"] == "failed"
    assert item["error_type"] == "archive_corrupt"
    assert item["retryable"] is False
    assert item["message"], "必须写明可读原因"
    assert item["display_name"] == "corrupt.zip"
    assert job["archive_summaries"], "archive_summaries 要说明该包发生了什么"
    assert job["archive_summaries"][0]["archive"] == "corrupt.zip"
    assert "失败" in job["archive_summaries"][0]["note"]
    assert "未能导入" in job["summary_text"]


def test_encrypted_zip_yields_failed_item(tmp_path, isolated_import):
    svc = isolated_import
    enc = tmp_path / "enc.zip"
    make_zip(str(enc), [("secret.txt", b"secret")])
    force_zip_encrypted_flag(str(enc))

    result = svc.create_import_job("学科A", [("enc.zip", enc.read_bytes())], "u1")
    job = _wait(svc, result["job_id"])
    assert job["status"] == "failed"
    assert job["items"][0]["error_type"] == "archive_encrypted"
    assert job["items"][0]["retryable"] is False


def test_archive_summary_explains_skipped_entries(tmp_path, isolated_import):
    svc = isolated_import
    archive = tmp_path / "pack.zip"
    make_zip(str(archive), [
        ("第1章/课件.txt", ("第一章内容。" * 30).encode("utf-8")),
        ("__MACOSX/._课件.txt", b"junk"),
        ("~$课件.txt", b"tmp"),
        ("Thumbs.db", b"x"),
    ])
    result = svc.create_import_job("学科A", [("pack.zip", archive.read_bytes())], "u1")
    job = _wait(svc, result["job_id"])
    assert job["status"] == "succeeded"

    statuses = {i["display_name"]: i for i in job["items"]}
    assert statuses["第1章__课件.txt"]["status"] == "ready"
    for name in ("__MACOSX/._课件.txt", "~$课件.txt", "Thumbs.db"):
        assert name in statuses, name
        assert statuses[name]["status"] == "skipped"
        assert statuses[name]["message"], "跳过项要给原因"

    summary = job["archive_summaries"][0]
    assert summary["extracted"] == 1
    assert summary["skipped"] == 3
    assert "忽略" in summary["note"]


@pytest.mark.integration
@pytest.mark.slow
def test_all_failed_batch_does_not_destroy_existing_kb(tmp_path, monkeypatch):
    """全部失败的一批不得破坏已有知识库（真实建库 + 真实检索）。"""
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VISION_MIN_IMAGE_BYTES", str(10 ** 9))

    from rag import rag_service
    monkeypatch.setattr(rag_service, "_VECTOR_STORE_BASE",
                        str(tmp_path / "vector_store"))
    monkeypatch.setattr(rag_service, "_SUBJECTS_DATA_BASE",
                        str(tmp_path / "subjects"))

    from rag.vector_store import GlobalFAISS
    from rag.hybrid_retriever import clear_index
    GlobalFAISS.clear_all()
    clear_index("学科B")

    from services import import_service
    from rag import retrieve_context

    txt = make_txt(str(tmp_path / "已有资料.txt"), "已有资料内容。" * 30)
    r1 = import_service.create_import_job(
        "学科B", [("已有资料.txt", txt.read_bytes())], "u1")
    job1 = _wait(import_service, r1["job_id"], timeout=300)
    assert job1["status"] == "succeeded", job1
    assert job1["kb"]["ready"] is True
    before = job1["kb"]["document_count"]
    assert before >= 1
    assert retrieve_context("已有资料内容", subject_id="学科B", top_k=3)

    # 提交必然失败的批次
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"not a zip at all")
    r2 = import_service.create_import_job("学科B", [("corrupt.zip", bad.read_bytes())], "u1")
    job2 = _wait(import_service, r2["job_id"], timeout=120)
    assert job2["status"] == "failed"
    assert job2["items"][0]["error_type"] == "archive_corrupt"

    # 已有资料与知识库都必须完好
    assert (tmp_path / "subjects" / "学科B" / "已有资料.txt").exists()
    assert job2["kb"]["ready"] is True, "既有知识库不得因本批失败变为不可用"
    assert job2["kb"]["document_count"] >= before
    assert retrieve_context("已有资料内容", subject_id="学科B", top_k=3), \
        "既有资料必须仍可检索"
