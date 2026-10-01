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

from tests.fixtures_isolated import IsolatedDataTestCase, register_and_login


class ConversationAPITest(IsolatedDataTestCase):
    """
    历史对话 API 测试

    阶段一补验：
    - 改用隔离夹具（临时库），不再写正式 data/chat.db
    - 会话是**个人数据**，接口已要求登录并做对象级归属校验，
      因此用例先登录并携带令牌（这正是生产前端的做法）
    """

    SUBJECTS = ("数据结构", "大学物理")

    def setUp(self):
        super().setUp()
        from api.app import app
        self.client = TestClient(app)
        self.token = register_and_login("conv_user")
        self.auth = {"X-User-Token": self.token}

    def test_create_and_list_conversation(self):
        """创建会话 → 列表包含，标题默认新对话"""
        client = self.client
        resp = client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        conv = resp.json()["data"]
        self.assertEqual(conv["subject_id"], "数据结构")
        self.assertEqual(conv["title"], "新对话")

        resp2 = client.get("/api/conversations", headers=self.auth)
        self.assertEqual(resp2.status_code, 200)
        ids = [c["id"] for c in resp2.json()["data"]]
        self.assertIn(conv["id"], ids)

    def test_list_filter_by_subject(self):
        """按学科过滤会话（会话隔离基础）"""
        client = self.client
        client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth)
        client.post("/api/conversations", json={"subject_id": "大学物理"}, headers=self.auth)

        resp = client.get("/api/conversations", params={"subject_id": "数据结构"}, headers=self.auth)
        self.assertEqual(len(resp.json()["data"]), 1)
        self.assertEqual(resp.json()["data"][0]["subject_id"], "数据结构")

    def test_conversation_isolation(self):
        """会话隔离：数据结构会话不会进入大学物理会话的上下文"""
        client = self.client
        r1 = client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth)
        r2 = client.post("/api/conversations", json={"subject_id": "大学物理"}, headers=self.auth)
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

            client.post("/api/chat/stream", headers=self.auth, json={
                "question": "什么是二叉树", "subject_id": "数据结构",
                "conversation_id": conv1})

        # 读取两个会话的消息，确认隔离
        msgs1 = client.get(f"/api/conversations/{conv1}/messages", headers=self.auth).json()["data"]
        msgs2 = client.get(f"/api/conversations/{conv2}/messages", headers=self.auth).json()["data"]
        self.assertEqual(len(msgs1), 2)  # user + assistant
        self.assertEqual(len(msgs2), 0)  # 大学物理会话无消息
        self.assertEqual(msgs1[0]["role"], "user")
        self.assertEqual(msgs1[0]["content"], "什么是二叉树")
        self.assertEqual(msgs1[1]["role"], "assistant")

    def test_conversation_title_auto_generated(self):
        """标题默认使用第一条问题截取（不调用 LLM）"""
        client = self.client
        r = client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth)
        conv_id = r.json()["data"]["id"]

        def _stream_generator():
            yield "答"

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=_stream_generator()):
            client.post("/api/chat/stream", headers=self.auth, json={
                "question": "帮我讲解二叉树的基本性质", "subject_id": "数据结构",
                "conversation_id": conv_id})

        detail = client.get(f"/api/conversations/{conv_id}", headers=self.auth).json()["data"]
        self.assertEqual(detail["title"], "帮我讲解二叉树的基本性质")

    def test_delete_conversation(self):
        """删除会话 → 404 再获取"""
        client = self.client
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth).json()["data"]["id"]

        resp = client.delete(f"/api/conversations/{conv_id}", headers=self.auth)
        self.assertEqual(resp.status_code, 200)

        resp2 = client.get(f"/api/conversations/{conv_id}", headers=self.auth)
        self.assertEqual(resp2.status_code, 404)

    def test_reload_messages_after_reconnect(self):
        """重启后消息仍存在（服务端 SQLite 持久化，非 localStorage）"""
        client = self.client
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth).json()["data"]["id"]

        # 模拟一次真实消息写入
        from services.conversation_service import add_message
        add_message(conv_id, "user", "第一问", message_type="chat")
        add_message(conv_id, "assistant", "第一答", message_type="chat")

        # 重新创建一个 client（模拟重连/重启，验证数据来自服务端而非前端内存）
        from fastapi.testclient import TestClient
        from api.app import app
        client2 = TestClient(app)
        msgs = client2.get(f"/api/conversations/{conv_id}/messages",
                           headers=self.auth).json()["data"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "第一问")

    def test_structured_message_payload_restore(self):
        """结构化消息：payload 持久化后可恢复（Card 渲染数据不丢）"""
        client = self.client
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}, headers=self.auth).json()["data"]["id"]

        from services.conversation_service import add_message
        note_payload = {"title": "二叉树笔记", "concept": {"content": "概念", "sources": []}}
        add_message(conv_id, "user", "智能笔记：二叉树", message_type="smart_note")
        add_message(conv_id, "assistant", "二叉树笔记", message_type="smart_note", payload=note_payload)

        msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=self.auth).json()["data"]
        assistant = [m for m in msgs if m["role"] == "assistant"][0]
        self.assertEqual(assistant["message_type"], "smart_note")
        self.assertEqual(assistant["payload"]["title"], "二叉树笔记")


if __name__ == "__main__":
    unittest.main()
