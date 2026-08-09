# -*- coding: utf-8 -*-
"""系统响应性并发回归测试（V2.5.2）

用 httpx.ASGITransport 在【同一事件循环】内模拟生产 uvicorn 形态。
长任务（Mock 同步 LLM）阻塞事件循环期间，/api/health/ready 必须快速返回。

测量方法：测试协程与长任务共享同一个事件循环，因此不能在循环内等待后测量
（自身也会被阻塞）。改为：Mock 先 set 一个 threading.Event 再 time.sleep，
由【独立线程】经 asyncio.run_coroutine_threadsafe 提交 /health 请求，
用 future.result(timeout) 检测是否被阻塞 —— 阻塞则超时（失败），
修复后事件循环空闲则毫秒级返回（通过）。

说明：不用 TestClient——它默认每个请求新建独立事件循环（portal），
测不出"单事件循环被同步阻塞"这一生产问题。

覆盖：Chat 非流式 / Chat Streaming / Smart Note / Learning Plan / Knowledge Graph
额外：Streaming 取消传播（客户端中止 → 上游生成器被关闭，不继续产生 token）
"""

import asyncio
import concurrent.futures
import os
import sys
import threading
import time
from unittest import mock

import pytest
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.app import app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 长任务 Mock 睡眠时长（秒）
LONG_TASK_SLEEP = 4
# 轻量接口允许的最大响应耗时（毫秒）
THRESHOLD_MS = 500

# 知识图谱测试学科（与真实数据隔离）
GRAPH_SUBJECT = "test_resp_graph"

BASE_URL = "http://test"


@pytest.fixture
async def client():
    """同一事件循环内的 ASGI 客户端（共享 loop = 生产形态）"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        try:
            await c.get("/api/health/ready")
        except Exception:
            pass
        yield c


async def _assert_health_responsive(client, launch_long, entered, path="/api/health/ready"):
    """
    在长任务阻塞事件循环期间，从独立线程测量 path 的响应性。

    Args:
        client: 共享 loop 上的 ASGI 客户端
        launch_long: 在共享 loop 上启动长任务的协程工厂（async callable）
        entered: threading.Event —— 长任务 mock 进入阻塞前 set
        path: 轻量接口路径

    Returns:
        (ms, ok)
    """
    loop = asyncio.get_running_loop()
    long_task = asyncio.create_task(launch_long())

    def _measure():
        # 等待 mock 进入阻塞区（此时 loop 即将/正在被占住）
        if not entered.wait(timeout=2.0):
            return None, False
        fut = asyncio.run_coroutine_threadsafe(client.get(path), loop)
        start = time.monotonic()
        try:
            fut.result(timeout=THRESHOLD_MS / 1000)
            ms = (time.monotonic() - start) * 1000
            return ms, ms < THRESHOLD_MS
        except concurrent.futures.TimeoutError:
            ms = (time.monotonic() - start) * 1000
            return ms, False

    ms, ok = await asyncio.to_thread(_measure)
    await long_task
    return ms, ok


def _make_slow_llm(entered, sleep_s=LONG_TASK_SLEEP, answer="测试回答"):
    """同步慢 LLM mock：先 set entered 再 time.sleep（真实阻塞，模拟 requests）"""
    def _call(prompt, system_message=None, model=None, **kw):
        entered.set()
        time.sleep(sleep_s)
        return answer
    return _call


# ===== 1. Chat 非流式 =====

@pytest.mark.anyio
async def test_health_responds_during_chat_nonstream(client):
    """Chat 非流式（同步 LLM 4s）期间 /api/health/ready 必须快速返回"""
    entered = threading.Event()
    with mock.patch("chat_service.call_llm", side_effect=_make_slow_llm(entered)), \
         mock.patch("chat_service.get_rag_context", return_value=[]), \
         mock.patch("chat_service.build_context", return_value=""), \
         mock.patch("chat_service.extract_sources", return_value=[]):
        ms, ok = await _assert_health_responsive(
            client,
            lambda: client.post("/api/chat", json={"question": "q", "subject_id": "数据结构"}),
            entered)

    assert ok, f"/api/health/ready 被 Chat 非流式阻塞 {ms:.0f}ms"


@pytest.mark.anyio
async def test_subjects_respond_during_chat_nonstream(client):
    """Chat 非流式期间 /api/subjects 也必须正常响应"""
    entered = threading.Event()
    with mock.patch("chat_service.call_llm", side_effect=_make_slow_llm(entered)), \
         mock.patch("chat_service.get_rag_context", return_value=[]), \
         mock.patch("chat_service.build_context", return_value=""), \
         mock.patch("chat_service.extract_sources", return_value=[]):
        ms, ok = await _assert_health_responsive(
            client,
            lambda: client.post("/api/chat", json={"question": "q", "subject_id": "数据结构"}),
            entered, path="/api/subjects")

    assert ok, f"/api/subjects 被 Chat 非流式阻塞 {ms:.0f}ms"


# ===== 2. Chat Streaming =====

@pytest.mark.anyio
async def test_health_responds_during_chat_streaming(client):
    """Chat Streaming（LLM 流 4s）期间 /api/health/ready 必须快速返回"""
    entered = threading.Event()

    def _slow_stream(provider, messages, temperature=None, max_tokens=None):
        entered.set()
        time.sleep(LONG_TASK_SLEEP)
        return iter(["测试回答"])

    async def _long_stream():
        async with client.stream("POST", "/api/chat/stream", json={
            "question": "q", "subject_id": "数据结构"}) as r:
            await r.aread()

    with mock.patch("chat_service.get_rag_context", return_value=[]), \
         mock.patch("chat_service.build_context", return_value=""), \
         mock.patch("chat_service.extract_sources", return_value=[]), \
         mock.patch("chat_service._call_llm_stream", side_effect=_slow_stream):
        ms, ok = await _assert_health_responsive(client, _long_stream, entered)

    assert ok, f"/api/health/ready 被 Chat Streaming 阻塞 {ms:.0f}ms"


# ===== 3. Smart Note =====

@pytest.mark.anyio
async def test_health_responds_during_smart_note(client):
    """Smart Note（同步 LLM 4s）期间 /api/health/ready 必须快速返回"""
    note_json = ('{"title":"笔记","concept":{"content":"c"},"example":{"content":"e"},'
                 '"mistakes":{"content":"m"},"class_tips":{"content":"t"},'
                 '"quiz":[{"question":"q","type":"single_choice","options":["A"],'
                 '"answer":"A","explanation":"x","knowledge_point":"k"}]}')
    entered = threading.Event()

    def _slow(prompt, system_message=None, model=None, **kw):
        entered.set()
        time.sleep(LONG_TASK_SLEEP)
        return note_json

    with mock.patch("services.note_service.call_llm", side_effect=_slow), \
         mock.patch("services.note_service.retrieve_and_build_context",
                    return_value={"context": "证据", "context_used": True, "sources": []}), \
         mock.patch("services.note_service.require_kb_ready", return_value=None):
        ms, ok = await _assert_health_responsive(
            client,
            lambda: client.post("/api/notes/generate",
                                json={"subject_id": "数据结构", "topic": "二叉树"}),
            entered)

    assert ok, f"/api/health/ready 被 Smart Note 阻塞 {ms:.0f}ms"


# ===== 4. Learning Plan =====

@pytest.mark.anyio
async def test_health_responds_during_learning_plan(client):
    """Learning Plan 生成（同步 LLM 4s）期间 /api/health/ready 必须快速返回"""
    from services import learning_service
    test_db = os.path.join(PROJECT_ROOT, "data", "test_resp_learning.db")
    old_db = learning_service.DB_PATH
    learning_service.DB_PATH = test_db
    learning_service._init_db()
    try:
        plan_json = ('{"title":"计划","goal":"g",'
                     '"days":[{"day":1,"tasks":[{"knowledge_point":"k","task_type":"READ",'
                     '"title":"t","description":"d","estimated_minutes":30}]}]}')
        entered = threading.Event()

        def _slow(prompt, system_message=None, model=None, **kw):
            entered.set()
            time.sleep(LONG_TASK_SLEEP)
            return plan_json

        with mock.patch("services.learning_service.call_llm", side_effect=_slow), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "x", "context_used": True, "sources": []}), \
             mock.patch("services.learning_service.require_kb_ready", return_value=None):
            ms, ok = await _assert_health_responsive(
                client,
                lambda: client.post("/api/learning/plans/generate",
                                    json={"subject_id": "数据结构", "goal": "复习"}),
                entered)

        assert ok, f"/api/health/ready 被 Learning Plan 阻塞 {ms:.0f}ms"
    finally:
        learning_service.DB_PATH = old_db
        for p in (test_db, test_db + "-wal", test_db + "-shm"):
            if os.path.exists(p):
                os.remove(p)


# ===== 5. Knowledge Graph（已后台化，验证不阻塞）=====

def _ensure_graph_stub():
    from rag.rag_service import _safe_dir_name
    d = os.path.join(PROJECT_ROOT, "data", "vector_store", _safe_dir_name(GRAPH_SUBJECT))
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "index.pkl")
    if not os.path.exists(p):
        with open(p, "wb") as f:
            f.write(b"stub")


def _clean_graph_stub():
    import shutil
    from rag.rag_service import _safe_dir_name
    d = os.path.join(PROJECT_ROOT, "data", "vector_store", _safe_dir_name(GRAPH_SUBJECT))
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)


async def _wait_graph_done(client, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = await client.get(f"/api/knowledge/{GRAPH_SUBJECT}/graph/status")
            st = r.json().get("data", {}).get("status")
            if st in ("success", "failed", "interrupted", "idle"):
                return st
        except Exception:
            pass
        await asyncio.sleep(0.3)
    return "timeout"


@pytest.mark.anyio
async def test_health_responds_during_knowledge_graph_build(client):
    """知识图谱构建（后台线程池 4s）期间 /api/health/ready 必须快速返回"""
    _ensure_graph_stub()
    try:
        entered = threading.Event()

        def _slow_build(subject_id, model=None, max_chunks=None, progress_cb=None, **kw):
            entered.set()
            time.sleep(LONG_TASK_SLEEP)

        with mock.patch(
                "rag.knowledge_graph.graph_builder.build_knowledge_graph",
                side_effect=_slow_build):
            ms, ok = await _assert_health_responsive(
                client,
                lambda: client.post(f"/api/knowledge/{GRAPH_SUBJECT}/graph/build"),
                entered)
            await _wait_graph_done(client)

        assert ok, f"/api/health/ready 被 Knowledge Graph 阻塞 {ms:.0f}ms"
    finally:
        _clean_graph_stub()


@pytest.mark.anyio
async def test_chat_available_during_knowledge_graph(client):
    """知识图谱正在生成时，Chat 仍可正常启动并完成"""
    _ensure_graph_stub()
    try:
        def _slow_build(subject_id, model=None, max_chunks=None, progress_cb=None, **kw):
            time.sleep(LONG_TASK_SLEEP)

        def _fast_stream(provider, messages, temperature=None, max_tokens=None):
            return iter(["图谱构建期间的回答"])

        with mock.patch(
                "rag.knowledge_graph.graph_builder.build_knowledge_graph",
                side_effect=_slow_build), \
             mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", side_effect=_fast_stream):
            long_task = asyncio.create_task(client.post(
                f"/api/knowledge/{GRAPH_SUBJECT}/graph/build"))
            await asyncio.sleep(0.5)

            start = time.monotonic()
            async with client.stream("POST", "/api/chat/stream", json={
                "question": "q", "subject_id": "数据结构"}) as r:
                content = await r.aread()
            ms = (time.monotonic() - start) * 1000
            await long_task
            await _wait_graph_done(client)

        assert ms < THRESHOLD_MS + 1500, f"Chat 流式在知识图谱构建期间耗时 {ms:.0f}ms（被阻塞）"
        assert "图谱构建期间的回答" in content.decode("utf-8", errors="ignore")
    finally:
        _clean_graph_stub()


# ===== Streaming 取消传播 =====

@pytest.mark.anyio
async def test_client_abort_closes_upstream(client):
    """客户端中断流 → 后台读取线程停止并关闭上游生成器（不再产生 token）"""
    closed = {"value": False}

    def _gen():
        try:
            for i in range(50):
                time.sleep(0.1)
                yield f"chunk{i}"
        finally:
            closed["value"] = True

    with mock.patch("chat_service.get_rag_context", return_value=[]), \
         mock.patch("chat_service.build_context", return_value=""), \
         mock.patch("chat_service.extract_sources", return_value=[]), \
         mock.patch("chat_service._call_llm_stream", return_value=_gen()):
        async with client.stream("POST", "/api/chat/stream", json={
            "question": "q", "subject_id": "数据结构"}) as r:
            async for line in r.aiter_text():
                if "chunk" in line:
                    break

    # 等待取消传播到后台读取线程（限时 1s）
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not closed["value"]:
        await asyncio.sleep(0.1)

    assert closed["value"], "客户端中止后上游生成器未被关闭（取消未传播，继续消耗 token）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
