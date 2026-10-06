"""
批量导入的"可恢复、可解释、提速"回归测试（2026-10-07）

对应真实事故：16 个成员只处理 8 个却报 succeeded；超大图必然失败；
单文件拖死整批；无法继续处理。
"""

import json
import time
from pathlib import Path

import pytest

from tests.import_samples import make_pptx, make_txt, make_zip


@pytest.fixture
def import_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    from services import import_service, knowledge_service

    calls = []

    def fake_build(subject_id, documents):
        calls.append((subject_id, len(documents)))
        return {"success": True, "subject_id": subject_id,
                "document_count": len(documents), "chunk_count": len(documents)}

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


def _fake_clock(seq):
    """按序返回时间值，用完后保持最后一个值（确定性触发整批预算）"""
    values = iter(seq)
    last = [seq[-1]]

    def fake():
        try:
            last[0] = next(values)
        except StopIteration:
            pass
        return last[0]

    return fake


def _make_three_txt_zip(tmp_path):
    arc = tmp_path / "三份资料.zip"
    make_zip(str(arc), [
        ("第一份.txt", ("第一章内容。" * 30).encode("utf-8")),
        ("第二份.txt", ("第二章内容。" * 30).encode("utf-8")),
        ("第三份.txt", ("第三章内容。" * 30).encode("utf-8")),
    ])
    return arc


# ==================== 任务 1：状态与计数 ====================


def test_partial_by_total_limit_then_continue(import_env, monkeypatch):
    """整个任务总上限到点：未处理成员可见、job=partial、可继续到全部 ready。

    2026-10-08 语义变更：此处改为验证 **IMPORT_MAX_SECONDS（整个任务上限）** 到点的行为；
    单批边界由 IMPORT_BATCH_SECONDS 控制且会自动接续下一批，不再把剩余丢给用户。
    """
    svc, tmp = import_env["svc"], import_env["tmp"]
    limits = dict(svc._limits())
    limits["max_seconds"] = 200          # 整个任务总上限
    monkeypatch.setattr(svc, "_limits", lambda: limits)

    arc = _make_three_txt_zip(tmp)

    # 时钟：call1 started=0；call2 总检查=1；call3 批起点=2；call4 第1份边界=3；
    #        call5 第1份总检查=3；call6 第2份边界=300（>240 → 本批结束，仅 1 份）；
    #        call7 下一轮总检查=300（>200 → 剩余未处理）。
    monkeypatch.setattr(svc, "_monotonic",
                        _fake_clock([0.0, 1.0, 2.0, 3.0, 3.0, 300.0]))

    result = svc.create_import_job("学科A", [("三份资料.zip", arc.read_bytes())], "u1")
    job = _wait(svc, result["job_id"])

    assert job["status"] == "partial", job
    totals = job["totals"]
    assert totals["total"] == 3
    assert totals["ready"] == 1
    assert totals["unprocessed"] == 2
    assert totals["failed"] == 0
    assert totals["skipped"] == 0
    assert (totals["total"] == totals["ready"] + totals["processing"]
            + totals["failed"] + totals["unprocessed"] + totals["skipped"])
    # 未处理成员没有任何 item（此前完全不可见）
    assert len(job["items"]) == 1
    assert "未处理" in job["summary_text"]
    assert job["can_continue"] is True
    assert job["batch_index"] >= 1
    assert any("总上限" in w for w in job["warnings"])

    # ---- 继续处理：恢复正常时钟与预算，只处理剩余 2 份 ----
    monkeypatch.setattr(svc, "_monotonic", time.monotonic)
    limits["max_seconds"] = 7200
    new_id = svc.continue_job(result["job_id"], "u1")
    assert new_id and new_id != result["job_id"]
    job2 = _wait(svc, new_id)

    assert job2["status"] == "succeeded", job2
    assert job2["totals"]["ready"] == 2
    assert job2["totals"]["unprocessed"] == 0
    assert job2["totals"]["total"] == 2
    assert job2["can_continue"] is False

    # 幂等：不产生重复资料
    names = sorted(p.name for p in (tmp / "subjects" / "学科A").iterdir())
    assert names == ["第一份.txt", "第三份.txt", "第二份.txt"], names

    # 刷新页面可见"可继续"的来源任务仍可查（跨重启语义靠库）
    listed = svc.list_jobs("学科A", 5)
    ids = {j["job_id"] for j in listed}
    assert result["job_id"] in ids and new_id in ids


def test_succeeded_requires_no_unprocessed(import_env):
    """全部成员处理完才允许 succeeded（回归事故：8/16 也报 succeeded）。"""
    svc = import_env["svc"]
    arc = _make_three_txt_zip(import_env["tmp"])
    result = svc.create_import_job("学科A", [("三份资料.zip", arc.read_bytes())], "u1")
    job = _wait(svc, result["job_id"])
    assert job["status"] == "succeeded"
    t = job["totals"]
    assert (t["files"], t["ready"], t["processing"], t["failed"],
            t["unprocessed"], t["skipped"], t["total"]) == (3, 3, 0, 0, 0, 0, 3)
    # 任务 4：分类计数（发现条目 / 容器 / 目录 / 有效资料）只增不改
    assert t["members_discovered"] == 3
    assert t["materials"] == 3
    assert t["containers"] == 1
    assert t["directories"] == 0
    assert job["can_continue"] is False


# ==================== 任务 3：超时与卡死 ====================


def test_single_item_timeout_does_not_block_others(import_env, monkeypatch):
    """单个文件超时记为 failed/item_timeout 并继续处理后面的文件。"""
    svc = import_env["svc"]
    monkeypatch.setenv("IMPORT_ITEM_MAX_SECONDS", "0.4")
    tmp = import_env["tmp"]
    arc = tmp / "含慢文件.zip"
    make_zip(str(arc), [
        ("慢.txt", ("慢文件内容。" * 30).encode("utf-8")),
        ("快1.txt", ("快文件一内容。" * 30).encode("utf-8")),
        ("快2.txt", ("快文件二内容。" * 30).encode("utf-8")),
    ])

    from rag.document import load_document_result

    real = svc._parse_file

    def slow_parse(path):
        if path.name == "慢.txt":
            time.sleep(1.5)
        return real(path)

    monkeypatch.setattr(svc, "_parse_file", slow_parse)

    result = svc.create_import_job("学科A", [("含慢文件.zip", arc.read_bytes())], "u1")
    job = _wait(svc, result["job_id"], timeout=60)

    slow = [i for i in job["items"] if i["display_name"] == "慢.txt"]
    ready = [i for i in job["items"] if i["status"] == "ready"]
    assert slow and slow[0]["status"] == "failed"
    assert slow[0]["error_type"] == "item_timeout"
    assert slow[0]["retryable"] is True
    assert len(ready) == 2, [i["display_name"] for i in job["items"]]
    assert job["status"] == "partial"
    assert job["can_continue"] is True
    assert job["totals"]["failed"] == 1
    assert job["totals"]["unprocessed"] == 0


# ==================== 任务 5：计时 ====================


def test_timings_present_and_non_negative(import_env):
    svc = import_env["svc"]
    arc = _make_three_txt_zip(import_env["tmp"])
    result = svc.create_import_job("学科A", [("三份资料.zip", arc.read_bytes())], "u1")
    job = _wait(svc, result["job_id"])

    for key in ("extract_ms", "parse_ms", "build_ms", "total_ms", "vision_ms"):
        assert key in job["stage_timings"], key
        assert job["stage_timings"][key] >= 0
    for item in job["items"]:
        timings = item["timings"]
        assert isinstance(timings, dict)
        for key in ("parse_ms", "vision_ms", "vision_calls", "vision_cached",
                    "images_total"):
            assert key in timings, (item["display_name"], key)
            assert timings[key] >= 0


# ==================== 任务 4：视觉提速 ====================


def _vision_provider_stub(calls, sleep=0.0, active=None, lock=None):
    class Stub:
        model_name = "stub-vision"

        def analyze_image(self, image_path):
            calls.append(image_path)
            if active is not None and lock is not None:
                with lock:
                    active[0] += 1
                    active[1] = max(active[1], active[0])
                time.sleep(sleep)
                with lock:
                    active[0] -= 1
            elif sleep:
                time.sleep(sleep)
            return {"description": "描述", "ocr_text": "", "model_used": "stub",
                    "provider_used": "stub", "vision_status": "success",
                    "error": None, "is_content": True}

    return Stub()


def _image_doc(path, page=1):
    from langchain_core.documents import Document
    return Document(page_content="", metadata={
        "modality": "image", "image_path": str(path),
        "file_type": "png", "page": page})


def test_scaled_copy_respects_provider_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    from PIL import Image
    from services.multimodal_service import (
        MAX_IMAGE_DIMENSION, MAX_IMAGE_PIXELS, _scale_to_limits, _scaled_copy)

    assert _scale_to_limits(2000, 1000) == 1.0
    assert _scale_to_limits(6000, 2000) < 1.0

    big = tmp_path / "big.png"
    Image.new("RGB", (6000, 2000), (10, 20, 30)).save(big)
    original_bytes = big.read_bytes()

    result = _scaled_copy(str(big), 6000, 2000)
    assert "error" not in result, result
    scaled = Image.open(result["path"])
    assert max(scaled.size) <= MAX_IMAGE_DIMENSION
    assert scaled.size[0] * scaled.size[1] <= MAX_IMAGE_PIXELS
    # 原图不动，只生成送审副本
    assert big.read_bytes() == original_bytes


def test_enricher_dedups_identical_images(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    from rag.enrichment.multimodal_enricher import MultimodalEnricher

    img_a = tmp_path / "a.png"
    img_b = tmp_path / "b.png"
    img_a.write_bytes(b"identical-image-bytes")
    img_b.write_bytes(b"identical-image-bytes")
    calls = []
    enricher = MultimodalEnricher(_vision_provider_stub(calls))
    docs = enricher.enrich_images([_image_doc(img_a, 1), _image_doc(img_b, 2)])

    assert len(calls) == 1, calls  # 相同内容只识别一次
    assert enricher.last_stats["images_total"] == 2
    assert enricher.last_stats["images_deduped"] == 1
    for doc in docs:
        assert doc.metadata["description"] == "描述"
        assert doc.metadata["page"] in (1, 2)  # 各自 metadata 不被覆盖


def test_enricher_per_image_timeout_does_not_block_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VISION_IMAGE_TIMEOUT_SECONDS", "0.3")
    from rag.enrichment.multimodal_enricher import MultimodalEnricher

    paths = []
    for i in range(2):
        p = tmp_path / f"slow{i}.png"
        p.write_bytes(f"distinct-{i}".encode())
        paths.append(p)
    enricher = MultimodalEnricher(_vision_provider_stub([], sleep=2.0))
    started = time.monotonic()
    docs = enricher.enrich_images([_image_doc(p) for p in paths])
    elapsed = time.monotonic() - started

    assert elapsed < 1.8, f"单张超时未生效，耗时 {elapsed:.2f}s"
    for doc in docs:
        assert doc.metadata["vision_status"] == "failed"
        assert doc.metadata["vision_error_type"] == "vision_timeout"


def test_enricher_controlled_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VISION_MAX_CONCURRENCY", "3")
    import threading
    from rag.enrichment.multimodal_enricher import MultimodalEnricher

    paths = []
    for i in range(3):
        p = tmp_path / f"c{i}.png"
        p.write_bytes(f"concurrency-{i}".encode())
        paths.append(p)
    active = [0, 0]
    lock = threading.Lock()
    calls = []
    enricher = MultimodalEnricher(
        _vision_provider_stub(calls, sleep=0.5, active=active, lock=lock))
    enricher.enrich_images([_image_doc(p) for p in paths])

    assert len(calls) == 3
    assert active[1] >= 2, f"并发未生效，最大并发 {active[1]}"


def test_vision_config_helpers(tmp_path, monkeypatch):
    from rag.enrichment import multimodal_enricher as me
    monkeypatch.setenv("VISION_MAX_CONCURRENCY", "5")
    monkeypatch.setenv("VISION_IMAGE_TIMEOUT_SECONDS", "7")
    assert me._vision_max_concurrency() == 5
    assert me._vision_image_timeout() == 7.0


# ==================== 任务 2 / 额外：受控解包目录 ====================


def test_archive_staging_uses_controlled_dir(tmp_path, monkeypatch):
    """解包暂存不得用系统 TEMP（事故：TEMP 下中文目录 Permission denied）。"""
    pytest.importorskip("py7zr")
    from tests.import_samples import make_7z
    from services import archive_service

    seven = tmp_path / "包.7z"
    make_7z(str(seven), [("第一份.txt", b"hello-7z")])

    staging_root = tmp_path / "extracted"
    staging_root.mkdir()
    made = archive_service._make_staging(staging_root, "rag_7z_")
    assert made.exists() and made.parent == staging_root

    def boom(*args, **kwargs):
        raise RuntimeError("系统 TEMP 不可用")

    monkeypatch.setattr(archive_service.tempfile, "mkdtemp", boom)
    result = archive_service.extract_archive(
        str(seven), str(staging_root / "out"),
        limits={"max_seconds": 30, "max_files": 10, "max_depth": 2,
                "max_total_bytes": 50 * 1024 * 1024,
                "max_extract_file_bytes": 10 * 1024 * 1024})
    assert len(result["entries"]) == 1
    assert result["entries"][0]["source_path"].endswith("第一份.txt")




# ==================== 真实事故 2：7z 目录被当作文件 ====================


def test_7z_directories_not_treated_as_files(tmp_path):
    """
    回归真实事故：py7zr 的 getnames() 对目录不带结尾斜杠，
    旧实现用 name.endswith("/") 判目录，把 12 个目录当文件 read_bytes()，
    在 Windows 抛 Permission denied 并被误判成 archive_corrupt。

    这里断言：目录绝不产出条目/失败，子文件全部导入，空目录不影响结果。
    """
    pytest.importorskip("py7zr")
    import py7zr
    from services import archive_service

    arc = tmp_path / "含目录.7z"
    with py7zr.SevenZipFile(str(arc), "w") as z:
        z.writestr(b"first", "章节A/同名.txt")
        z.writestr(b"second", "章节B/同名.txt")
        z.writestr(b"deep", "章节A/子目录/深层.txt")
        empty = tmp_path / "空目录"
        empty.mkdir()
        try:
            z.write(str(empty), "章节A/空目录")
        except Exception:
            pass  # 某些 py7zr 版本不支持写空目录；目录判定仍由上面的子目录覆盖

    dest = tmp_path / "out"
    result = archive_service.extract_archive(
        str(arc), str(dest),
        limits={"max_total_bytes": 10 * 1024 * 1024,
                "max_extract_file_bytes": 1024 * 1024,
                "max_files": 50, "max_depth": 2, "max_seconds": 60})
    entries = result["entries"]
    assert len(entries) == 3, [e["source_path"] for e in entries]
    for entry in entries:
        assert Path(entry["abs_path"]).is_file()
    names = {e["source_path"] for e in entries}
    assert names == {"章节A/同名.txt", "章节B/同名.txt", "章节A/子目录/深层.txt"}
    display = {e["display_name"] for e in entries}
    assert "章节A__同名.txt" in display and "章节B__同名.txt" in display
    # 目录不得造成任何 "损坏/不可读" 类失败
    bad = [s for s in result["skipped"]
           if (s.get("error_type") or "") in
           ("archive_corrupt", "archive_member_unreadable", "archive_permission")]
    assert bad == [], bad


def test_7z_member_read_failure_is_isolated(tmp_path, monkeypatch):
    """单成员读取失败只跳过该成员，其余继续，不误报整包 archive_corrupt。"""
    pytest.importorskip("py7zr")
    import py7zr
    from services import archive_service

    arc = tmp_path / "两份.7z"
    with py7zr.SevenZipFile(str(arc), "w") as z:
        z.writestr(b"bad-member", "读不出的.txt")
        z.writestr(b"good-member", "正常.txt")

    # 让其中一个成员在解压后读取时报权限错误
    real_read_bytes = Path.read_bytes

    def flaky_read_bytes(self):
        if self.name == "读不出的.txt":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)
    result = archive_service.extract_archive(
        str(arc), str(tmp_path / "out"),
        limits={"max_total_bytes": 10 * 1024 * 1024,
                "max_extract_file_bytes": 1024 * 1024,
                "max_files": 50, "max_depth": 2, "max_seconds": 60})
    monkeypatch.setattr(Path, "read_bytes", real_read_bytes)

    ok = [e for e in result["entries"] if e["source_path"] == "正常.txt"]
    assert len(ok) == 1, result
    skipped = [s for s in result["skipped"] if s["source_path"] == "读不出的.txt"]
    assert skipped and skipped[0]["error_type"] == "archive_member_unreadable"
    assert not any(e["source_path"] == "读不出的.txt" for e in result["entries"])


REAL_7Z = Path(
    r"D:\神の文档\langchain-rag-work\demo_data\import_tmp"
    r"\imp_5ae1033b535e\raw\000_莫大物化.7z")


@pytest.mark.integration
@pytest.mark.slow
def test_real_7z_package_no_directory_failures(tmp_path):
    """
    真实原包（287MB，72 文件 + 12 目录 + 1 个嵌套 zip）解压回归。
    只做解压（不做整包解析/建库，避免测试过慢）；断言 12 个目录没有造成任何 failed。
    原包不在本机时跳过。
    """
    if not REAL_7Z.exists():
        pytest.skip("真实原包不存在（非本机环境）")
    from services import archive_service

    mb = 1024 * 1024
    result = archive_service.extract_archive(
        str(REAL_7Z), str(tmp_path / "out"),
        limits={"max_total_bytes": 2048 * mb, "max_extract_file_bytes": 300 * mb,
                "max_files": 200, "max_depth": 2, "max_seconds": 600})
    assert result["entries"], result
    for entry in result["entries"]:
        assert Path(entry["abs_path"]).is_file()
    assert any("1/1.jpg" in e["source_path"] for e in result["entries"])
    assert any("::" in e["source_path"] for e in result["entries"])  # 嵌套 zip 已展开
    bad = [s for s in result["skipped"]
           if (s.get("error_type") or "") in
           ("archive_corrupt", "archive_member_unreadable", "archive_permission")]
    assert bad == [], bad


def test_continue_route_registered():
    from api.routes import knowledge
    paths = {r.path for r in knowledge.router.routes}
    assert "/knowledge/imports/{job_id}/continue" in paths

def test_legacy_job_backfills_totals_from_archive_summaries(import_env, monkeypatch):
    """
    旧任务（没有 plan_json）必须用 archive_summaries.extracted 回填 total/unprocessed。

    真实事故 imp_d3557678d5b5：archive_summaries.extracted=16，但只有 8 条 item，
    旧代码把它记为 succeeded 且 totals.total=8 —— 用户于是看到
    "已完成 / 8 份可学习"，完全不知道还有 8 份没进来。
    修复后：total=16、unprocessed=8、status=partial、can_continue=true，
    且**不改写数据库里的历史行**。
    """
    import json as _json
    import sqlite3 as _sqlite3

    svc, tmp = import_env["svc"], import_env["tmp"]
    limits = dict(svc._limits())
    limits["max_seconds"] = 300
    monkeypatch.setattr(svc, "_limits", lambda: limits)

    arc = _make_three_txt_zip(tmp)
    result = svc.create_import_job("学科旧", [("三份资料.zip", arc.read_bytes())], "u9")
    job = _wait(svc, result["job_id"])
    assert job["status"] == "succeeded"
    jid = result["job_id"]

    # 模拟"本改动之前的旧任务"：清掉 plan_json，archive_summaries 写成 16 个成员，
    # status 保持旧代码会写的 succeeded，warnings 保留超时说明。
    db = str(svc._db_path())
    conn = _sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE import_jobs SET plan_json='[]', status='succeeded',"
            " archive_summaries_json=?, warnings_json=?, totals_json=? WHERE job_id=?",
            (_json.dumps([{"archive": "三份资料.zip", "extracted": 16, "skipped": 0}],
                         ensure_ascii=False),
             _json.dumps(["解析耗时超过上限 300s，剩余资料未处理"], ensure_ascii=False),
             _json.dumps({"files": 3, "ready": 3, "failed": 0, "skipped": 0, "bytes": 1},
                         ensure_ascii=False),
             jid))
        conn.commit()
        before = tuple(conn.execute(
            "SELECT status, totals_json FROM import_jobs WHERE job_id=?", (jid,)).fetchone())
    finally:
        conn.close()

    view = svc.get_job(jid)
    tot = view["totals"]
    assert tot["total"] == 16, tot
    assert tot["unprocessed"] == 13, tot          # 16 - 3 条 item
    assert tot["ready"] == 3, tot
    assert (tot["ready"] + tot["processing"] + tot["failed"]
            + tot["unprocessed"] + tot["skipped"]) == tot["total"], tot
    assert view["status"] == "partial", view["status"]
    assert view["can_continue"] is True
    assert "未处理" in (view["summary_text"] or "")

    # 只改返回视图，不改历史行
    conn = _sqlite3.connect(db)
    try:
        after = tuple(conn.execute(
            "SELECT status, totals_json FROM import_jobs WHERE job_id=?", (jid,)).fetchone())
    finally:
        conn.close()
    assert after == before, (before, after)


def test_legacy_failed_job_can_continue(import_env):
    """旧任务整体失败（如被误判 archive_corrupt 的 7z）也应可"继续"重跑。"""
    import sqlite3 as _sqlite3

    svc, tmp = import_env["svc"], import_env["tmp"]
    svc._init_db()
    job_id = "imp_legacy_failed"
    raw_dir = tmp / "raw_failed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    with _sqlite3.connect(str(svc._db_path())) as conn:
        conn.execute(
            "INSERT INTO import_jobs (job_id, subject_id, user_id, status, created_at,"
            " updated_at, finished_at, totals_json, warnings_json,"
            " archive_summaries_json, kb_json, summary_text, raw_dir)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "学科旧", "u1", "failed", "2026-10-07T19:50:30",
             "2026-10-07T19:56:00", "2026-10-07T19:56:00",
             '{"files": 1, "ready": 0, "failed": 1, "skipped": 0, "bytes": 1}',
             '["压缩包处理失败（archive_corrupt）"]',
             '[{"archive": "莫大物化.7z", "extracted": 0, "skipped": 0,'
             ' "note": "失败：Permission denied"}]',
             "{}", "没有可导入的资料", str(raw_dir)))
        conn.execute(
            "INSERT INTO import_items (item_id, job_id, seq, display_name,"
            " source_path, origin_archive, size, status, stage, progress,"
            " message, doc_id, error_type, retryable, stored_path, content_sha)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("itm_legacy_failed", job_id, 0, "莫大物化.7z", "莫大物化.7z",
             "莫大物化.7z", 1, "failed", "parsing", None,
             "压缩包处理失败", None, "archive_corrupt", 0, None, None))
        conn.commit()

    job = svc.get_job(job_id)
    assert job["status"] == "failed"       # 整体失败仍是 failed，不伪造成 partial
    assert job["can_continue"] is True     # 但允许用修好的解包器继续重跑

# ==================== rag 栈导入串行化（跨线程模块锁死锁回归） ====================


def test_load_rag_stack_is_idempotent_and_serialized():
    """
    load_rag_stack() 必须在进程锁内完成整条 rag 栈的首次导入，且可重入、幂等。

    回归事故（2026-10-07）：继续导入任务在后台线程里导入 knowledge_service，
    触发 "from rag.rag_service import ..."，与预热线程并发进入 import 机器，
    Python 3.14 抛 deadlock detected by _ModuleLock('rag.rag_service')，
    任务以"任务执行异常"失败且 0 条目。
    """
    import sys
    import threading

    from services.rag_loader import load_rag_stack

    first = load_rag_stack()
    assert first is load_rag_stack()
    # 子模块确实已被定型（否则线程仍可能各自进入 import 机器）
    assert "rag.rag_service" in sys.modules

    errors = []

    def worker():
        try:
            for _ in range(5):
                assert load_rag_stack() is first
        except Exception as e:      # pragma: no cover - 失败时记录以便报错
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert not errors, errors


def test_reconcile_interrupted_jobs_marks_partial_and_clears_inflight(import_env):
    """
    服务重启后，停在 queued/running 的任务必须被收敛为 partial + 可继续，
    且"进行中"的条目不能永远挂在 processing 上。

    回归事故（2026-10-07）：整机内存压力下后端进程被杀，正在运行的导入任务
    永远停在 status='running'：页面只显示"处理中"，没有进度也没有「继续处理」入口，
    而且这条任务会挡住后续 continue 链。
    """
    import sqlite3 as _sqlite3

    svc, tmp = import_env["svc"], import_env["tmp"]
    arc = _make_three_txt_zip(tmp)
    result = svc.create_import_job("学科中断", [("三份资料.zip", arc.read_bytes())], "u7")
    jid = result["job_id"]
    _wait(svc, jid)

    # 人为制造"进程被杀"的残留：任务 running + 条目停留在 indexing
    db = str(svc._db_path())
    conn = _sqlite3.connect(db)
    try:
        conn.execute("UPDATE import_jobs SET status='running', finished_at=NULL WHERE job_id=?",
                     (jid,))
        conn.execute("UPDATE import_items SET status='indexing' WHERE job_id=?", (jid,))
        conn.commit()
    finally:
        conn.close()

    fixed = svc.reconcile_interrupted_jobs()
    assert fixed >= 1

    view = svc.get_job(jid)
    assert view["status"] == "partial", view["status"]
    assert view["can_continue"] is True
    assert view["totals"]["processing"] == 0, view["totals"]
    assert "中断" in (view["summary_text"] or ""), view["summary_text"]
    assert any(i["status"] == "failed" and i.get("retryable") for i in view["items"]),         [(i["status"], i.get("retryable")) for i in view["items"]]

    # 幂等：已收敛的任务不会被重复计数
    assert svc.reconcile_interrupted_jobs() == 0
