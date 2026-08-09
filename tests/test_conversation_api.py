# -*- coding: utf-8 -*-
"""历史对话系统 API 测试（V2.5.1 Checkpoint A/B）

Conversation + Message 持久化：创建、列表、按学科分类、加载消息、删除、会话隔离、重启持久化。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from services import conversation_service

TEST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "test_conversation_api.db")


def _make_client():
    from api.app import app
    return TestClient(app)


class ConversationAPITest(unittest.TestCase):
    def setUp(self):
        conversation_service.DB_PATH = TEST_DB
        conversation_service._init_db()

    def tearDown(self):
        for p in (TEST_DB, TEST_DB + "-wal", TEST_DB + "-shm"):
            if os.path.exists(p):
                os.remove(p)

    def test_create_and_list_conversation(self):
        """创建会话 → 列表包含，标题默认新对话"""
        client = _make_client()
        resp = client.post("/api/conversations", json={"subject_id": "数据结构"})
        self.assertEqual(resp.status_code, 200, resp.text)
        conv = resp.json()["data"]
        self.assertEqual(conv["subject_id"], "数据结构")
        self.assertEqual(conv["title"], "新对话")

        resp2 = client.get("/api/conversations")
        self.assertEqual(resp2.status_code, 200)
        ids = [c["id"] for c in resp2.json()["data"]]
        self.assertIn(conv["id"], ids)

    def test_list_filter_by_subject(self):
        """按学科过滤会话（会话隔离基础）"""
        client = _make_client()
        client.post("/api/conversations", json={"subject_id": "数据结构"})
        client.post("/api/conversations", json={"subject_id": "大学物理"})

        resp = client.get("/api/conversations", params={"subject_id": "数据结构"})
        self.assertEqual(len(resp.json()["data"]), 1)
        self.assertEqual(resp.json()["data"][0]["subject_id"], "数据结构")

    def test_conversation_isolation(self):
        """会话隔离：数据结构会话不会进入大学物理会话的上下文"""
        client = _make_client()
        r1 = client.post("/api/conversations", json={"subject_id": "数据结构"})
        r2 = client.post("/api/conversations", json={"subject_id": "大学物理"})
        conv1 = r1.json()["data"]["id"]
        conv2 = r2.json()["data"]["id"]

        # 通过流式接口写入不同会话消息（mock LLM + KB 守卫放行）
        # _call_llm_stream 期望 yield 文本 chunk（chat_service 内部处理）
        def _stream_generator():
            yield "数据结构回答"

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=_stream_generator()):

            client.post("/api/chat/stream", json={
                "question": "什么是二叉树", "subject_id": "数据结构",
                "conversation_id": conv1})

        # 读取两个会话的消息，确认隔离
        msgs1 = client.get(f"/api/conversations/{conv1}/messages").json()["data"]
        msgs2 = client.get(f"/api/conversations/{conv2}/messages").json()["data"]
        self.assertEqual(len(msgs1), 2)  # user + assistant
        self.assertEqual(len(msgs2), 0)  # 大学物理会话无消息
        self.assertEqual(msgs1[0]["role"], "user")
        self.assertEqual(msgs1[0]["content"], "什么是二叉树")
        self.assertEqual(msgs1[1]["role"], "assistant")

    def test_conversation_title_auto_generated(self):
        """标题默认使用第一条问题截取（不调用 LLM）"""
        client = _make_client()
        r = client.post("/api/conversations", json={"subject_id": "数据结构"})
        conv_id = r.json()["data"]["id"]

        def _stream_generator():
            yield "答"

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=_stream_generator()):
            client.post("/api/chat/stream", json={
                "question": "帮我讲解二叉树的基本性质", "subject_id": "数据结构",
                "conversation_id": conv_id})

        detail = client.get(f"/api/conversations/{conv_id}").json()["data"]
        self.assertEqual(detail["title"], "帮我讲解二叉树的基本性质")

    def test_delete_conversation(self):
        """删除会话 → 404 再获取"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        resp = client.delete(f"/api/conversations/{conv_id}")
        self.assertEqual(resp.status_code, 200)

        resp2 = client.get(f"/api/conversations/{conv_id}")
        self.assertEqual(resp2.status_code, 404)

    def test_reload_messages_after_reconnect(self):
        """重启后消息仍存在（服务端 SQLite 持久化，非 localStorage）"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        # 模拟一次真实消息写入
        from services.conversation_service import add_message
        add_message(conv_id, "user", "第一问", message_type="chat")
        add_message(conv_id, "assistant", "第一答", message_type="chat")

        # 重新创建 client（模拟重启）
        client2 = _make_client()
        msgs = client2.get(f"/api/conversations/{conv_id}/messages").json()["data"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "第一问")

    def test_structured_message_payload_restore(self):
        """结构化消息：payload 持久化后可恢复（Card 渲染数据不丢）"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        from services.conversation_service import add_message
        note_payload = {"title": "二叉树笔记", "concept": {"content": "概念", "sources": []}}
        add_message(conv_id, "user", "智能笔记：二叉树", message_type="smart_note")
        add_message(conv_id, "assistant", "二叉树笔记", message_type="smart_note", payload=note_payload)

        msgs = client.get(f"/api/conversations/{conv_id}/messages").json()["data"]
        assistant = [m for m in msgs if m["role"] == "assistant"][0]
        self.assertEqual(assistant["message_type"], "smart_note")
        self.assertEqual(assistant["payload"]["title"], "二叉树笔记")


if __name__ == "__main__":
    unittest.main()
