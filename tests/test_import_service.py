"""
批量导入任务测试（2026-10-07）

覆盖：状态机、条目状态、压缩包展开与同名不覆盖、
部分失败不破坏已有资料、建库失败可重试且幂等、取消、能力清单路由。
"""

import time

import pytest

from tests.import_samples import make_pptx, make_txt, make_xlsx, make_zip


@pytest.fixture
def import_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    from services import import_service, knowledge_service

    calls = []

    def fake_build(subject_id, documents):
        calls.append((subject_id, len(documents)))
        return {"success": True, "subject_id": subject_id,
                "document_count": len(documents)}

    monkeypatch.setattr(knowledge_service,
                        "rebuild_subject_knowledge_from_documents", fake_build)
    return {"svc": import_service, "ks": knowledge_service,
            "calls": calls, "tmp": tmp_path}


def _wait(svc, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = svc.get_job(job_id)
        if job and job["status"] in svc.TERMINAL_JOB_STATUSES:
            return job
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束: {svc.get_job(job_id)}")


def test_create_and_run_success_single_build(import_env):
    svc, tmp = import_env["svc"], import_env["tmp"]
    pptx = make_pptx(str(tmp / "课件.pptx"), with_image=False)
    xlsx = make_xlsx(str(tmp / "成绩.xlsx"))
    result = svc.create_import_job("学科A", [
        ("课件.pptx", pptx.read_bytes()),
        ("成绩.xlsx", xlsx.read_bytes()),
    ], "u1")
    assert result["accepted"] == 2
    assert result["rejected"] == []

    job = _wait(svc, result["job_id"])
    assert job["status"] == "succeeded"
    assert job["totals"]["ready"] == 2
    assert job["totals"]["failed"] == 0
    assert all(i["status"] == "ready" for i in job["items"])
    assert all(i["doc_id"].startswith("f:") for i in job["items"])
    # 一批资料只能建一次库
    assert len(import_env["calls"]) == 1
    assert import_env["calls"][0][1] > 0
    assert (tmp / "subjects" / "学科A" / "课件.pptx").exists()
    assert (tmp / "subjects" / "学科A" / "成绩.xlsx").exists()


def test_unsupported_file_rejected(import_env):
    svc = import_env["svc"]
    result = svc.create_import_job("学科A", [("bad.xyz", b"x")], "u1")
    assert result["accepted"] == 0
    assert result["rejected"][0]["error_type"] == "unsupported_format"
    job = svc.get_job(result["job_id"])
    assert job["status"] == "failed"
    assert job["summary_text"]


def test_converter_unavailable_rejected_with_instructions(import_env):
    from rag import formats
    if formats.converter_capabilities()["available"]:
        pytest.skip("本机已有 Office 转换组件（LibreOffice 或 Microsoft Office），"
                    "无法验证不可用分支；停用分支见 tests/test_office_converter.py")
    svc = import_env["svc"]
    result = svc.create_import_job("学科A", [("旧课件.ppt", b"binary")], "u1")
    assert result["accepted"] == 0
    rejected = result["rejected"][0]
    assert rejected["error_type"] == "converter_unavailable"
    assert "LibreOffice" in rejected["reason"]
    assert "OFFICE_CONVERTER_PATH" in rejected["reason"]


def test_archive_same_name_not_overwritten(import_env):
    svc, tmp = import_env["svc"], import_env["tmp"]
    archive = tmp / "资料包.zip"
    make_zip(str(archive), [
        ("第1章/内容.txt", ("第一章内容。" * 30).encode("utf-8")),
        ("第2章/内容.txt", ("第二章内容。" * 30).encode("utf-8")),
    ])
    result = svc.create_import_job("学科A", [("资料包.zip", archive.read_bytes())], "u1")
    assert result["accepted"] == 1
    job = _wait(svc, result["job_id"])
    assert job["status"] == "succeeded"
    names = sorted((tmp / "subjects" / "学科A").iterdir())
    names = sorted(p.name for p in names)
    assert names == ["第1章__内容.txt", "第2章__内容.txt"]
    a = (tmp / "subjects" / "学科A" / "第1章__内容.txt").read_text(encoding="utf-8")
    b = (tmp / "subjects" / "学科A" / "第2章__内容.txt").read_text(encoding="utf-8")
    assert a != b
    assert job["archive_summaries"][0]["extracted"] == 2


def test_build_failure_preserves_existing_and_retry_idempotent(import_env, monkeypatch):
    svc, ks, tmp = import_env["svc"], import_env["ks"], import_env["tmp"]
    first = make_txt(str(tmp / "第一份.txt"), "第一份资料内容。" * 30)
    res1 = svc.create_import_job("学科A", [("第一份.txt", first.read_bytes())], "u1")
    job1 = _wait(svc, res1["job_id"])
    assert job1["status"] == "succeeded"
    existing = tmp / "subjects" / "学科A" / "第一份.txt"
    assert existing.exists()

    def boom(subject_id, documents):
        raise RuntimeError("模拟建库失败")

    monkeypatch.setattr(ks, "rebuild_subject_knowledge_from_documents", boom)
    second = make_txt(str(tmp / "第二份.txt"), "第二份资料内容。" * 30)
    res2 = svc.create_import_job("学科A", [("第二份.txt", second.read_bytes())], "u1")
    job2 = _wait(svc, res2["job_id"])
    assert job2["status"] == "failed"
    assert job2["can_retry"] is True
    failed = [i for i in job2["items"] if i["error_type"] == "build_failed"]
    assert failed and failed[0]["retryable"] is True
    # 已有资料绝不因本批失败而消失
    assert existing.exists()
    assert (tmp / "subjects" / "学科A" / "第二份.txt").exists()

    # 修复后重试：成功，且不产生重复资料
    def fixed(subject_id, documents):
        return {"success": True, "subject_id": subject_id,
                "document_count": len(documents)}

    monkeypatch.setattr(ks, "rebuild_subject_knowledge_from_documents", fixed)
    new_id = svc.retry_job(res2["job_id"], "u1")
    assert new_id
    job3 = _wait(svc, new_id)
    assert job3["status"] in ("succeeded", "partial")
    assert job3["job_id"] == new_id
    assert job3["totals"]["failed"] == 0
    names = sorted(p.name for p in (tmp / "subjects" / "学科A").iterdir())
    assert names == ["第一份.txt", "第二份.txt"], names


def test_reimport_same_content_is_idempotent(import_env):
    svc, tmp = import_env["svc"], import_env["tmp"]
    txt = make_txt(str(tmp / "资料.txt"), "重复导入的资料内容。" * 30)
    res1 = svc.create_import_job("学科A", [("资料.txt", txt.read_bytes())], "u1")
    assert _wait(svc, res1["job_id"])["status"] == "succeeded"

    res2 = svc.create_import_job("学科A", [("资料.txt", txt.read_bytes())], "u1")
    job2 = _wait(svc, res2["job_id"])
    assert job2["status"] == "succeeded"
    assert job2["items"][0]["status"] == "ready"
    assert "同内容" in job2["items"][0]["message"]
    names = sorted(p.name for p in (tmp / "subjects" / "学科A").iterdir())
    assert names == ["资料.txt"]


def test_cancel_queued_job(import_env, monkeypatch):
    svc, tmp = import_env["svc"], import_env["tmp"]
    monkeypatch.setattr(svc, "_run_job", lambda job_id: None)  # 不真正执行
    result = svc.create_import_job("学科A", [("a.txt", b"hello world")], "u1")
    assert svc.cancel_job(result["job_id"]) is True
    job = svc.get_job(result["job_id"])
    assert job["status"] == "cancelled"
    assert svc.cleanup_job_files(result["job_id"]) is None


def test_list_jobs_returns_recent(import_env):
    svc, tmp = import_env["svc"], import_env["tmp"]
    txt = make_txt(str(tmp / "a.txt"), "内容。" * 40)
    svc.create_import_job("学科A", [("a.txt", txt.read_bytes())], "u1")
    _wait(svc, svc.list_jobs("学科A", 1)[0]["job_id"])
    jobs = svc.list_jobs("学科A", 5)
    assert jobs and jobs[0]["subject_id"] == "学科A"
    assert svc.list_jobs("别的学科", 5) == []


def test_import_routes_registered():
    from api.routes import knowledge
    paths = {r.path for r in knowledge.router.routes}
    for expected in ("/knowledge/capabilities",
                     "/knowledge/{subject_id}/import",
                     "/knowledge/imports/{job_id}",
                     "/knowledge/{subject_id}/imports",
                     "/knowledge/imports/{job_id}/retry"):
        assert expected in paths, expected


@pytest.mark.integration
@pytest.mark.slow
def test_pptx_and_xlsx_content_enters_retrieval(tmp_path, monkeypatch):
    """真实建库 + 真实检索：支持某格式必须意味着内容真的能被检索到。"""
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
    clear_index("检索学科")

    from services import import_service
    pptx = make_pptx(str(tmp_path / "课件.pptx"), with_image=False)
    xlsx = make_xlsx(str(tmp_path / "成绩.xlsx"))
    result = import_service.create_import_job("检索学科", [
        ("课件.pptx", pptx.read_bytes()),
        ("成绩.xlsx", xlsx.read_bytes()),
    ], "u1")
    job = _wait(import_service, result["job_id"], timeout=300)
    assert job["status"] == "succeeded", job
    assert job["kb"]["ready"] is True

    from rag import retrieve_context
    docs = retrieve_context("组合形状内文本是什么", subject_id="检索学科", top_k=3)
    blob = "\n".join(getattr(d, "page_content", "") for d in docs)
    assert "组合形状内文本" in blob or "alpha" in blob, blob

    # metadata 必须一路透传到检索结果：引用才能显示真实课件名与幻灯片号
    hit = next((d for d in docs
                if "组合形状内文本" in (getattr(d, "page_content", "") or "")), None)
    if hit is not None:
        assert hit.metadata.get("file_name") == "课件.pptx"
        assert hit.metadata.get("page") == 1
        assert hit.metadata.get("modality") == "slide"
