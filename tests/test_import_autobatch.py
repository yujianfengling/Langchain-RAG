"""
自动分批 / 单一执行者 / 暂停取消 / 分类计数 / 重试 / 内存峰值 / 超时中止 回归测试
（2026-10-08）

对应本轮目标：用户拖入资料包后系统自动处理到明确终态，不需要反复手动点“继续”。
全部使用隔离 tmp 数据目录 + 小样本，不触碰正式 data/ 与演示库。
"""

import time
from pathlib import Path

import pytest

from tests.import_samples import make_txt, make_zip


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


def _fake_clock(seq, tail=None):
    values = list(seq)
    last = [tail if tail is not None else values[-1]]

    def fake():
        if values:
            last[0] = values.pop(0)
        return last[0]

    return fake


def _multi_txt_zip(tmp_path, n=6, name="多份.zip"):
    arc = tmp_path / name
    make_zip(str(arc), [
        (f"文件{i}.txt", (f"第{i}份内容。" * 40).encode("utf-8")) for i in range(n)
    ])
    return arc


# ==================== 任务 1：自动分批，无需人工点击 ====================


def test_auto_batch_reaches_terminal_without_manual_click(import_env, monkeypatch):
    """多批任务自动跑到终态：batch_index>1 且全部 ready，无需 continue/resume。"""
    svc, tmp = import_env["svc"], import_env["tmp"]
    limits = dict(svc._limits())
    limits["max_seconds"] = 7200
    monkeypatch.setattr(svc, "_limits", lambda: limits)
    monkeypatch.setattr(svc, "_batch_sleep_seconds", lambda: 0.0)

    # 时钟强制切两批：批边界 240s。
    # call1 started=0；call2 总检查=1；call3 批1起点=2；call4 第1份边界=3；
    # call5 第1份总检查=3；call6 第2份边界=1000（>240 → 批1结束，1 份）；
    # call7 总检查=1000；call8 批2起点=1000；之后恒为 1000（批内不再越界）。
    monkeypatch.setattr(svc, "_monotonic",
                        _fake_clock([0.0, 1.0, 2.0, 3.0, 3.0, 1000.0, 1000.0, 1000.0]))

    arc = _multi_txt_zip(tmp, n=3)
    res = svc.create_import_job("学科AB", [("多份.zip", arc.read_bytes())], "u1")
    job = _wait(svc, res["job_id"], timeout=90)

    assert job["status"] == "succeeded", job
    assert job["batch_index"] >= 2, job["batch_index"]
    assert job["totals"]["ready"] == 3
    assert job["totals"]["unprocessed"] == 0
    assert job["can_continue"] is False
    assert not any("未处理" in str(w) for w in job["warnings"]), job["warnings"]


def test_auto_batch_guard_stops_on_no_progress(import_env, monkeypatch):
    """防失控：某批无任何新终态就停止，绝不无限循环。"""
    svc, tmp = import_env["svc"], import_env["tmp"]
    arc = _multi_txt_zip(tmp, n=2)
    # 让“终态计数”永远不变 → 第一批即判定无进展
    monkeypatch.setattr(svc, "_terminal_item_count", lambda job_id: 0)
    res = svc.create_import_job("学科NG", [("多份.zip", arc.read_bytes())], "u1")
    job = _wait(svc, res["job_id"], timeout=30)
    assert any("无进展" in str(w) for w in job["warnings"]), job["warnings"]
    # 检测到无进展后立即停止，不再进入下一批
    assert job["batch_index"] == 1, job["batch_index"]


# ==================== 任务 2：同一任务只有一个执行者 ====================


def test_single_executor_claim_and_takeover(import_env, monkeypatch):
    """被活跃执行者持有时拒绝执行；心跳超时可接管。"""
    svc, tmp = import_env["svc"], import_env["tmp"]
    # 阻止后台线程真正执行，任务保持 queued
    monkeypatch.setattr(svc, "_run_job_thread", lambda *a, **k: None)
    txt = make_txt(str(tmp / "a.txt"), "内容。" * 60)
    res = svc.create_import_job("学科EX", [("a.txt", txt.read_bytes())], "u1")
    jid = res["job_id"]

    assert svc._claim_executor(jid, "exec-A") is True
    assert svc._claim_executor(jid, "exec-B") is False   # 活跃持有 → 拒绝

    # 重复触发 _run_job：必须被拒绝，不产生任何 item
    svc._run_job(jid)
    job = svc.get_job(jid)
    assert job["items"] == []
    assert job["status"] == "queued"

    # 心跳超时 → 可被接管
    import sqlite3
    with sqlite3.connect(str(svc._db_path())) as conn:
        conn.execute("UPDATE import_jobs SET executor_heartbeat=? WHERE job_id=?",
                     (time.time() - 10 ** 6, jid))
        conn.commit()
    assert svc._claim_executor(jid, "exec-B") is True
    assert svc._claim_executor(jid, "exec-C") is False
    # 释放后可再次声明
    svc._release_executor(jid, "exec-B")
    assert svc._claim_executor(jid, "exec-C") is True


def test_reconcile_clears_executor_and_auto_resume_flag(import_env, monkeypatch):
    """服务重启：running 收敛为 partial、释放执行者声明；auto_resume 可接续。"""
    import sqlite3
    svc, tmp = import_env["svc"], import_env["tmp"]
    arc = _multi_txt_zip(tmp, n=3)
    res = svc.create_import_job("学科RS", [("多份.zip", arc.read_bytes())], "u1")
    jid = res["job_id"]
    _wait(svc, jid)

    with sqlite3.connect(str(svc._db_path())) as conn:
        conn.execute("UPDATE import_jobs SET status='running', finished_at=NULL,"
                     " executor_id='old', executor_heartbeat=0 WHERE job_id=?", (jid,))
        conn.execute("UPDATE import_items SET status='indexing' WHERE job_id=?", (jid,))
        conn.commit()

    fixed = svc.reconcile_interrupted_jobs()
    assert fixed >= 1
    job = svc.get_job(jid)
    assert job["status"] == "partial"
    assert svc._claim_executor(jid, "new-exec") is True  # 旧声明已清，可接管
    # 用户暂停的不自动恢复
    assert svc.pause_job(jid) is True
    started = svc.auto_resume_interrupted_jobs()
    assert started == 0
    assert svc.get_job(jid)["paused"] is True


# ==================== 任务 3：暂停 / 继续 / 取消 ====================


def _make_slow_env(svc, monkeypatch, real_parse):
    monkeypatch.setattr(svc, "_batch_seconds", lambda: 0.05)
    monkeypatch.setattr(svc, "_batch_sleep_seconds", lambda: 0.0)
    monkeypatch.setattr(svc, "_parse_file", lambda p: (time.sleep(0.15), real_parse(p))[1])


def _wait_items(svc, jid, at_least=1, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = svc.get_job(jid)
        if job and len(job["items"]) >= at_least:
            return job
        time.sleep(0.02)
    raise AssertionError(f"未在 {timeout}s 内产生 {at_least} 个 item")


def test_pause_does_not_auto_restart_then_resume(import_env, monkeypatch):
    svc, tmp = import_env["svc"], import_env["tmp"]
    real_parse = svc._parse_file
    _make_slow_env(svc, monkeypatch, real_parse)
    arc = _multi_txt_zip(tmp, n=6)
    res = svc.create_import_job("学科PA", [("多份.zip", arc.read_bytes())], "u1")
    jid = res["job_id"]
    _wait_items(svc, jid, at_least=1)

    assert svc.pause_job(jid) is True
    job = _wait(svc, jid, timeout=60)
    assert job["paused"] is True
    assert job["status"] == "partial"
    assert job["can_resume"] is True

    count = len(job["items"])
    time.sleep(0.5)
    stable = svc.get_job(jid)
    assert len(stable["items"]) == count, "暂停后不得自动重启"
    assert stable["paused"] is True

    assert svc.resume_job(jid) == jid
    done = _wait(svc, jid, timeout=90)
    assert done["status"] == "succeeded", done
    assert done["paused"] is False
    assert done["totals"]["ready"] == 6
    # 不产生重复资料
    names = sorted(p.name for p in (tmp / "subjects" / "学科PA").iterdir())
    assert len(names) == len(set(names)) == 6, names


def test_cancel_does_not_auto_restart(import_env, monkeypatch):
    svc, tmp = import_env["svc"], import_env["tmp"]
    real_parse = svc._parse_file
    _make_slow_env(svc, monkeypatch, real_parse)
    arc = _multi_txt_zip(tmp, n=6)
    res = svc.create_import_job("学科CA", [("多份.zip", arc.read_bytes())], "u1")
    jid = res["job_id"]
    _wait_items(svc, jid, at_least=1)

    assert svc.cancel_job(jid) is True
    job = _wait(svc, jid, timeout=60)
    assert job["status"] == "cancelled"
    assert job["paused"] is False
    assert job["can_resume"] is False
    count = len(job["items"])
    time.sleep(0.5)
    stable = svc.get_job(jid)
    assert stable["status"] == "cancelled"
    assert len(stable["items"]) == count, "取消后不得自动重启"


def test_import_control_routes_registered():
    from api.routes import knowledge
    paths = {r.path for r in knowledge.router.routes}
    for p in ("/knowledge/imports/{job_id}/pause",
              "/knowledge/imports/{job_id}/resume",
              "/knowledge/imports/{job_id}/cancel"):
        assert p in paths, p


# ==================== 任务 4：分类与计数 ====================


def test_discovery_counts_separate_members_containers_dirs_materials(import_env):
    """发现条目 / 容器 / 目录 / 有效资料分开计数；目录不算资料。"""
    svc, tmp = import_env["svc"], import_env["tmp"]
    inner = make_zip(str(tmp / "内层.zip"),
                     [("内层.txt", ("内层内容。" * 40).encode("utf-8"))])
    arc = tmp / "外层.zip"
    make_zip(str(arc), [
        ("顶层.txt", ("顶层内容。" * 40).encode("utf-8")),
        ("章节/", b""),
        ("章节/章节内容.txt", ("章节内容。" * 40).encode("utf-8")),
        ("内层.zip", inner.read_bytes()),
        ("__MACOSX/._x", b"junk"),
    ])
    res = svc.create_import_job("学科CNT", [("外层.zip", arc.read_bytes())], "u1")
    job = _wait(svc, res["job_id"], timeout=60)
    t = job["totals"]
    # 有效资料：顶层/章节内容/内层 共 3 份
    assert t["materials"] == 3, t
    assert t["ready"] == 3, t
    # 容器：外层 + 嵌套内层 = 2
    assert t["containers"] == 2, t
    # 目录：章节/ = 1（不是资料、不读取、不报错）
    assert t["directories"] == 1, t
    # 发现条目：3 个文件 + 1 个被跳过(__MACOSX) + 1 个嵌套容器 = 5
    assert t["members_discovered"] == 5, t
    # 分类计数不能被当成可学习资料数
    assert t["members_discovered"] != t["ready"], t
    assert (t["total"] == t["ready"] + t["processing"]
            + t["failed"] + t["unprocessed"] + t["skipped"]), t
    assert with_item_error_type(job, "archive_corrupt") is False


def with_item_error_type(job, error_type):
    return any(i.get("error_type") == error_type for i in job["items"])


# ==================== 任务 5：重试有界且有分类 ====================


def test_retries_bounded_and_non_retryable_not_retried(import_env, monkeypatch):
    svc = import_env["svc"]
    monkeypatch.setenv("IMPORT_ITEM_MAX_RETRIES", "2")
    monkeypatch.setenv("IMPORT_RETRY_BACKOFF_SECONDS", "0.001")
    calls = {"n": 0}

    def transient(path, timeout):
        calls["n"] += 1
        return [], "connection_error", "模拟瞬时网络错误", {
            "parse_ms": 1, "vision_stats": {}, "item_timed_out": False}

    monkeypatch.setattr(svc, "_parse_with_timeout", transient)
    docs, et, msg, meta = svc._parse_item_with_retries(Path("x.txt"))
    assert et == "connection_error"
    assert calls["n"] == 3, calls        # 1 次 + 最多 2 次重试
    assert meta["retries"] == 2

    calls["n"] = 0

    def auth_fail(path, timeout):
        calls["n"] += 1
        return [], "invalid_api_key", "401 凭证失效", {
            "parse_ms": 1, "vision_stats": {}, "item_timed_out": False}

    monkeypatch.setattr(svc, "_parse_with_timeout", auth_fail)
    svc._parse_item_with_retries(Path("y.txt"))
    assert calls["n"] == 1, calls        # 凭据失效不重试

    calls["n"] = 0

    def unsupported(path, timeout):
        calls["n"] += 1
        return [], "unsupported_format", "不支持", {
            "parse_ms": 1, "vision_stats": {}, "item_timed_out": False}

    monkeypatch.setattr(svc, "_parse_with_timeout", unsupported)
    svc._parse_item_with_retries(Path("z.txt"))
    assert calls["n"] == 1, calls        # 明确不支持不重试


# ==================== 任务 6：内存峰值与超时后不再派发新工作 ====================


def test_memory_peak_fields_present_and_non_negative(import_env):
    svc, tmp = import_env["svc"], import_env["tmp"]
    txt = make_txt(str(tmp / "mem.txt"), "内存峰值测试内容。" * 60)
    res = svc.create_import_job("学科MEM", [("mem.txt", txt.read_bytes())], "u1")
    job = _wait(svc, res["job_id"], timeout=60)
    st = job["stage_timings"]
    for key in ("parse_memory_peak_mb", "build_memory_peak_mb", "rss_peak_mb"):
        assert key in st, (key, st)
        assert st[key] >= 0, (key, st[key])
    assert len(job["items"]) == 1
    timings = job["items"][0]["timings"]
    assert "memory_peak_mb" in timings and timings["memory_peak_mb"] >= 0
    assert "rss_mb" in timings and timings["rss_mb"] >= 0


def test_item_timeout_aborts_further_vision_dispatch(import_env, monkeypatch):
    """单文件超时后不再派发新工作：被放弃的解析线程看到 abort 即停止。"""
    svc = import_env["svc"]
    monkeypatch.setenv("IMPORT_ITEM_MAX_SECONDS", "0.3")
    observed = {"abort": False, "ticks": 0}

    def slow_parse(path):
        for _ in range(400):
            if svc._vision_abort_requested():
                observed["abort"] = True
                return [], "parse_error", "aborted", {"vision_stats": {}}
            observed["ticks"] += 1
            time.sleep(0.02)
        return [], "parse_error", "loop-end", {"vision_stats": {}}

    monkeypatch.setattr(svc, "_parse_file", slow_parse)
    docs, et, msg, meta = svc._parse_with_timeout(Path("scan.txt"), 0.3)
    assert et == "item_timeout"
    assert meta["item_timed_out"] is True

    deadline = time.time() + 5
    while not observed["abort"] and time.time() < deadline:
        time.sleep(0.02)
    assert observed["abort"] is True, "超时后未置位 abort"
    ticks = observed["ticks"]
    time.sleep(0.25)
    assert observed["ticks"] == ticks, "超时后仍在继续派发新工作"

    # 增强器同样能读到该中止信号（惰性转发）
    from rag.enrichment.multimodal_enricher import _vision_abort_requested
    assert isinstance(_vision_abort_requested(), bool)


def test_timings_present_and_non_negative_autobatch(import_env):
    svc, tmp = import_env["svc"], import_env["tmp"]
    arc = _multi_txt_zip(tmp, n=3)
    res = svc.create_import_job("学科TM", [("多份.zip", arc.read_bytes())], "u1")
    job = _wait(svc, res["job_id"], timeout=60)
    for key in ("extract_ms", "parse_ms", "build_ms", "total_ms", "vision_ms"):
        assert key in job["stage_timings"], key
        assert job["stage_timings"][key] >= 0
    for item in job["items"]:
        for key in ("parse_ms", "memory_peak_mb"):
            assert key in item["timings"], (item["display_name"], key)
            assert item["timings"][key] >= 0


def test_vision_max_concurrency_respects_inflight_cap(monkeypatch):
    from rag.enrichment import multimodal_enricher as me
    monkeypatch.setenv("VISION_MAX_CONCURRENCY", "5")
    monkeypatch.setenv("IMPORT_MAX_INFLIGHT", "2")
    assert me._vision_max_concurrency() == 2
    monkeypatch.delenv("IMPORT_MAX_INFLIGHT")
    assert me._vision_max_concurrency() == 5


def test_auto_resume_after_restart_reprocesses_interrupted(import_env):
    """重启恢复：reconcile 后 auto_resume 自动接续，被中断成员被重新处理直到终态。"""
    import sqlite3
    svc, tmp = import_env["svc"], import_env["tmp"]
    arc = _multi_txt_zip(tmp, n=3)
    res = svc.create_import_job("学科AR", [("多份.zip", arc.read_bytes())], "u1")
    jid = res["job_id"]
    _wait(svc, jid, timeout=60)

    with sqlite3.connect(str(svc._db_path())) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT item_id FROM import_items WHERE job_id=? ORDER BY seq", (jid,))]
        # 一个成员“没建 item”（未处理），另一个被中断
        conn.execute("DELETE FROM import_items WHERE item_id=?", (ids[0],))
        conn.execute("UPDATE import_items SET status='failed',"
                     " error_type='interrupted', retryable=1 WHERE item_id=?", (ids[1],))
        conn.execute("UPDATE import_jobs SET status='running', finished_at=NULL,"
                     " executor_id='old', executor_heartbeat=0 WHERE job_id=?", (jid,))
        conn.commit()

    assert svc.reconcile_interrupted_jobs() >= 1
    assert svc.auto_resume_interrupted_jobs() == 1
    done = _wait(svc, jid, timeout=90)
    assert done["status"] == "succeeded", done
    assert done["totals"]["ready"] == 3
    assert done["totals"]["unprocessed"] == 0
    assert done["totals"]["failed"] == 0
    names = sorted(p.name for p in (tmp / "subjects" / "学科AR").iterdir())
    assert len(names) == 3, names