"""
执行者围栏（fencing）与长任务心跳回归测试（2026-10-08）

对应本轮目标：
1. 旧执行者被接管后**绝不再写** import_items / 落盘 / 建库，并明确退出；
2. 长建库阻塞超过过期阈值时**不会**被误接管，任务照常完成；
3. 并发重复触发同一任务，只有一个是真正执行者，其余被明确拒绝；
4. 接管后新执行者能跑完，且不产生重复资料/条目。

全部使用隔离 tmp 数据目录 + 小样本，不触碰正式 data/ 与演示库。
"""

import sqlite3
import threading
import time

import pytest

from tests.import_samples import make_txt, make_zip


@pytest.fixture
def fence_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_DATA_DIR", str(tmp_path))
    from services import import_service, knowledge_service

    calls = []

    def fake_build(subject_id, documents):
        calls.append((subject_id, len(documents)))
        return {"success": True, "subject_id": subject_id,
                "document_count": len(documents), "chunk_count": len(documents)}

    monkeypatch.setattr(knowledge_service,
                        "rebuild_subject_knowledge_from_documents", fake_build)
    real_thread = import_service._run_job_thread
    # 创建任务时不自动起真实线程，由测试自己决定何时执行
    monkeypatch.setattr(import_service, "_run_job_thread", lambda *a, **k: None)
    return {"svc": import_service, "ks": knowledge_service, "calls": calls,
            "tmp": tmp_path, "real_thread": real_thread}


def _set_heartbeat(svc, jid, executor_id, heartbeat, epoch):
    with sqlite3.connect(str(svc._db_path())) as conn:
        conn.execute(
            "UPDATE import_jobs SET executor_id=?, executor_heartbeat=?,"
            " executor_epoch=? WHERE job_id=?",
            (executor_id, heartbeat, epoch, jid))
        conn.commit()


def _heartbeat_of(svc, jid):
    with sqlite3.connect(str(svc._db_path())) as conn:
        row = conn.execute(
            "SELECT executor_heartbeat FROM import_jobs WHERE job_id=?", (jid,)).fetchone()
    return row[0] if row else None


def _multi_txt_zip(tmp_path, n=3, name="多份.zip"):
    arc = tmp_path / name
    make_zip(str(arc), [
        (f"文件{i}.txt", (f"第{i}份内容。" * 40).encode("utf-8")) for i in range(n)
    ])
    return arc


# ==================== 1) 声明被接管：旧执行者停止写入并退出 ====================


def test_stale_executor_cannot_write_items_or_build(fence_env, monkeypatch, caplog):
    """解析途中被他人接管 → 不再落盘、不建库、不再写 item 终态，并有明确日志。"""
    svc, tmp = fence_env["svc"], fence_env["tmp"]
    txt = make_txt(str(tmp / "a.txt"), "正文内容。" * 60)
    res = svc.create_import_job("学科F1", [("a.txt", txt.read_bytes())], "u1")
    jid = res["job_id"]

    real_parse = svc._parse_file
    takeover = {}

    def parse_then_takeover(path):
        docs, error_type, message, meta = real_parse(path)
        # 模拟"心跳过期后被他人接管"：executor_id 换成别人且 epoch +1
        with sqlite3.connect(str(svc._db_path())) as conn:
            row = conn.execute(
                "SELECT executor_epoch FROM import_jobs WHERE job_id=?", (jid,)).fetchone()
        takeover["epoch"] = int(row[0] or 0) + 1
        _set_heartbeat(svc, jid, "intruder", time.time(), takeover["epoch"])
        return docs, error_type, message, meta

    monkeypatch.setattr(svc, "_parse_file", parse_then_takeover)
    with caplog.at_level("WARNING"):
        fence_env["real_thread"](jid)   # 同步跑完原执行者

    # 不建库
    assert fence_env["calls"] == [], "被接管后绝不能建库"
    # 不落盘
    subject_dir = tmp / "subjects" / "学科F1"
    landed = [p.name for p in subject_dir.iterdir()] if subject_dir.exists() else []
    assert landed == [], landed
    # 原执行者不再写终态：条目停在 parsing，绝不出现 ready/indexing
    job = svc.get_job(jid)
    statuses = {it["status"] for it in job["items"]}
    assert "ready" not in statuses and "indexing" not in statuses, job["items"]
    # 有明确日志，不静默
    assert any("fencing" in str(rec.message) for rec in caplog.records), \
        [rec.message for rec in caplog.records]
    # 接管者仍是当前执行者；旧执行者（换任何旧 id/epoch）都已失活
    assert svc._is_executor_current(jid, "intruder", takeover["epoch"]) is True
    assert svc._is_executor_current(jid, "intruder", takeover["epoch"] - 1) is False
    assert svc._is_executor_current(jid, "someone-else", takeover["epoch"]) is False


# ==================== 2) 长建库不被误判失活 ====================


def test_long_build_heartbeat_prevents_takeover(fence_env, monkeypatch):
    """建库阻塞远超过期阈值；独立看护线程持续续期 → 不被接管、任务照常完成。"""
    svc, tmp, ks = fence_env["svc"], fence_env["tmp"], fence_env["ks"]
    # 过期阈值 1.5s；心跳间隔 = timeout/5 = 0.3s
    monkeypatch.setattr(svc, "_executor_timeout_seconds", lambda: 1.5)

    arc = _multi_txt_zip(tmp, n=3, name="长建库.zip")
    res = svc.create_import_job("学科F2", [("长建库.zip", arc.read_bytes())], "u1")
    jid = res["job_id"]

    observed = {"beats": [], "intruder_claims": []}

    def slow_build(subject_id, documents):
        fence_env["calls"].append((subject_id, len(documents)))
        started = time.time()
        # 阻塞 4.0s（> 1.5s 过期阈值）：期间只有看护线程能续期心跳
        while time.time() - started < 4.0:
            observed["beats"].append(_heartbeat_of(svc, jid))
            time.sleep(0.2)
        # 建库期间尝试抢占：心跳是新鲜的，必须被拒绝
        observed["intruder_claims"].append(svc._claim_executor(jid, "intruder"))
        return {"success": True, "subject_id": subject_id,
                "document_count": len(documents), "chunk_count": len(documents)}

    monkeypatch.setattr(ks, "rebuild_subject_knowledge_from_documents", slow_build)
    fence_env["real_thread"](jid)

    job = svc.get_job(jid)
    assert job["status"] == "succeeded", job
    assert job["totals"]["ready"] == 3, job["totals"]
    # 看护线程在建库期间确实续期了心跳（多个不同值）
    beats = [b for b in observed["beats"] if b]
    assert len(set(beats)) >= 2, observed["beats"]
    # 长建库期间没有被误接管
    assert observed["intruder_claims"] == [False], observed["intruder_claims"]


# ==================== 3) 并发重复触发：只有一个执行者 ====================


def test_concurrent_duplicate_run_only_one_executor(fence_env, monkeypatch, caplog):
    """同一任务被并发触发 4 次：只有 1 个进入执行，其余被明确拒绝。"""
    svc, tmp = fence_env["svc"], fence_env["tmp"]
    txt = make_txt(str(tmp / "b.txt"), "并发内容。" * 60)
    res = svc.create_import_job("学科F3", [("b.txt", txt.read_bytes())], "u1")
    jid = res["job_id"]

    started = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def fake_locked(job_id, executor_id, only_keys=None, reuse_job_id=None, epoch=None):
        with lock:
            started.append(executor_id)
        time.sleep(0.5)

    monkeypatch.setattr(svc, "_run_job_locked", fake_locked)

    def trigger():
        barrier.wait()
        svc._run_job(jid)

    with caplog.at_level("WARNING"):
        threads = [threading.Thread(target=trigger) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(started) == 1, started
    assert any("拒绝重复执行" in str(rec.message) for rec in caplog.records), \
        [rec.message for rec in caplog.records]


# ==================== 4) 接管后新执行者跑完且不重复 ====================


def test_takeover_new_executor_completes_without_duplicates(fence_env, caplog):
    """旧声明（过期）被新执行者接管：新执行者正常完成，无重复条目/无重复文件。"""
    svc, tmp = fence_env["svc"], fence_env["tmp"]
    arc = _multi_txt_zip(tmp, n=3, name="接管.zip")
    res = svc.create_import_job("学科F4", [("接管.zip", arc.read_bytes())], "u1")
    jid = res["job_id"]

    # 伪造：一个旧执行者曾声明过，但心跳早已过期（epoch=1）
    _set_heartbeat(svc, jid, "old-exec", 0, 1)

    with caplog.at_level("WARNING"):
        fence_env["real_thread"](jid)   # 新执行者接管并跑完

    done = svc.get_job(jid)
    assert done["status"] == "succeeded", done
    assert done["totals"]["ready"] == 3, done["totals"]
    assert done["totals"]["failed"] == 0
    # 不产生重复条目
    items = done["items"]
    keys = [(it["origin_archive"], it["source_path"]) for it in items]
    assert len(items) == 3 and len(set(keys)) == 3, keys
    # 不产生重复资料文件
    names = sorted(p.name for p in (tmp / "subjects" / "学科F4").iterdir())
    assert len(names) == len(set(names)) == 3, names
    # 建库只发生一次，且只提交 3 份不重复的文档（无重复分块来源）
    assert len(fence_env["calls"]) == 1, fence_env["calls"]
    assert fence_env["calls"][0][1] == 3, fence_env["calls"]
    # 旧执行者的代数已失效（id 也不匹配）；旧执行者任何写入都被围栏拒绝
    assert svc._is_executor_current(jid, "old-exec", 1) is False
    svc._set_executor_fence(jid, "old-exec", 1)
    try:
        with pytest.raises(svc._ExecutorLostError):
            svc._insert_item(jid, 999, display_name="不应写入", status="pending")
    finally:
        svc._clear_executor_fence()
