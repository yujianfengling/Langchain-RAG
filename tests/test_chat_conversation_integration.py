# -*- coding: utf-8 -*-
"""Chat + Conversation 集成测试（V2.5.1 Checkpoint B）

- conversation_id 透传
- 流式完成 → 写入 user + assistant 消息
- 后端按 conversation_id 恢复历史（不信任前端 history）
- 中断时状态合理
- 结构化模式（smart_note/summary/explanation/quiz/advice）通过 Chat 入口工作
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from services import conversation_service

TEST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "test_chat_conv_integration.db")


def _make_client():
    from api.app import app
    return TestClient(app)


class ChatConversationIntegrationTest(unittest.TestCase):
    def setUp(self):
        conversation_service.DB_PATH = TEST_DB
        conversation_service._init_db()

    def tearDown(self):
        for p in (TEST_DB, TEST_DB + "-wal", TEST_DB + "-shm"):
            if os.path.exists(p):
                os.remove(p)

    def _patch_chat_stream(self, deltas=("你", "好", "世界")):
        """Mock _call_llm_stream：yield 文本 chunk（chat_service 内部只接收字符串）"""
        return iter(deltas)

    def test_stream_writes_user_and_assistant_messages(self):
        """流式完成后写入 user + assistant 消息"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=self._patch_chat_stream()):
            resp = client.post("/api/chat/stream", json={
                "question": "什么是二叉树？", "subject_id": "数据结构",
                "conversation_id": conv_id})

        self.assertEqual(resp.status_code, 200)
        msgs = conversation_service.list_messages(conv_id)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "什么是二叉树？")
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["content"], "你好世界")

    def test_conversation_id_required_for_persistence(self):
        """无 conversation_id → 不写库（不持久化）"""
        client = _make_client()
        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=self._patch_chat_stream()):
            resp = client.post("/api/chat/stream", json={
                "question": "你好", "subject_id": "数据结构"})

        self.assertEqual(resp.status_code, 200)
        convs = conversation_service.list_conversations()
        self.assertEqual(convs, [])

    def test_subject_mismatch_rejected(self):
        """会话学科与请求学科不一致 → error，不写消息"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        resp = client.post("/api/chat/stream", json={
            "question": "物理问题", "subject_id": "大学物理",
            "conversation_id": conv_id})

        # SSE 流式返回 200，error 事件在流体内
        body = resp.text
        self.assertIn("会话学科与当前学科不一致", body)
        msgs = conversation_service.list_messages(conv_id)
        self.assertEqual(msgs, [])

    def test_history_restored_from_db_not_frontend(self):
        """后端从 DB 恢复历史，不完全信任前端 history"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        # 先写入两条历史消息
        conversation_service.add_message(conv_id, "user", "第一问", message_type="chat")
        conversation_service.add_message(conv_id, "assistant", "第一答", message_type="chat")

        # 第二次发送，检查 _format_history 收到 DB 恢复的历史
        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=self._patch_chat_stream()) as mock_stream, \
             mock.patch("chat_service._format_history", wraps=lambda h, **k: "|".join(x["content"] for x in h)) as mock_fmt:
            client.post("/api/chat/stream", json={
                "question": "第二问", "subject_id": "数据结构",
                "conversation_id": conv_id,
                "history": [{"role": "user", "content": "前端伪造的历史"}]})

        # _format_history 应收到 DB 历史（第一问/第一答 + 刚写入的第二问），而非前端伪造内容
        call_args = mock_fmt.call_args[0][0] if mock_fmt.call_args else []
        contents = [m["content"] for m in call_args]
        self.assertIn("第一问", contents)
        self.assertNotIn("前端伪造的历史", contents)

    def test_interrupted_stream_reasonable_state(self):
        """流中断：user 消息已写入，assistant 未写入（状态合理）"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        def _broken_gen():
            yield {"type": "start", "model": "mimo"}
            yield {"type": "delta", "content": "部分内容"}
            raise RuntimeError("连接中断")

        with mock.patch("chat_service.get_rag_context", return_value=[]), \
             mock.patch("chat_service.build_context", return_value=""), \
             mock.patch("chat_service.extract_sources", return_value=[]), \
             mock.patch("chat_service._call_llm_stream", return_value=_broken_gen()):
            resp = client.post("/api/chat/stream", json={
                "question": "中断问题", "subject_id": "数据结构",
                "conversation_id": conv_id})

        self.assertEqual(resp.status_code, 200)
        msgs = conversation_service.list_messages(conv_id)
        # user 已持久化；assistant 因中断未写入（合理状态，不崩溃）
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")

    # ===== 结构化模式通过 Chat 入口 =====

    def _fake_llm_answer(self):
        return '{"title":"二叉树笔记","concept":{"content":"概念"},"quiz":[{"question":"q","type":"single_choice","options":["A","B"],"answer":"A","explanation":"e","knowledge_point":"kp"}]}'

    def test_smart_note_via_chat_entry(self):
        """Chat 智能笔记模式 → done 事件携带 message_type + payload，消息持久化"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        with mock.patch("services.note_service.call_llm", return_value=self._fake_llm_answer()), \
             mock.patch("services.note_service.retrieve_and_build_context",
                        return_value={"context": "二叉树证据", "context_used": True, "sources": [{"file_name": "树.pdf"}]}), \
             mock.patch("services.note_service.require_kb_ready", return_value=None):
            resp = client.post("/api/chat/stream", json={
                "question": "二叉树", "subject_id": "数据结构",
                "conversation_id": conv_id, "mode": "smart_note"})

        body = resp.text
        self.assertIn("smart_note", body)
        self.assertIn("二叉树笔记", body)

        msgs = conversation_service.list_messages(conv_id)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["message_type"], "smart_note")
        self.assertEqual(msgs[1]["message_type"], "smart_note")
        self.assertEqual(msgs[1]["payload"]["title"], "二叉树笔记")

    def test_summary_via_chat_entry(self):
        """Chat 知识总结模式 → payload 含 key_points/summary"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        def _fake_summary(prompt, system_message=None, model=None, **kw):
            return '{"key_points":[{"id":1,"title":"树","description":"d"}],"summary":"整体总结"}'

        with mock.patch("services.education_service.call_llm", side_effect=_fake_summary):
            resp = client.post("/api/chat/stream", json={
                "question": "总结树的定义", "subject_id": "数据结构",
                "conversation_id": conv_id, "mode": "summary"})

        body = resp.text
        self.assertIn("summary", body)
        self.assertIn("整体总结", body)
        msgs = conversation_service.list_messages(conv_id)
        self.assertEqual(msgs[1]["message_type"], "summary")
        self.assertEqual(msgs[1]["payload"]["key_points"][0]["title"], "树")

    def test_quiz_via_chat_entry(self):
        """Chat 自测模式 → payload 含 quiz 列表"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        with mock.patch("services.note_service.call_llm", return_value=self._fake_llm_answer()), \
             mock.patch("services.note_service.retrieve_and_build_context",
                        return_value={"context": "证据", "context_used": True, "sources": []}), \
             mock.patch("services.note_service.require_kb_ready", return_value=None):
            resp = client.post("/api/chat/stream", json={
                "question": "二叉树自测", "subject_id": "数据结构",
                "conversation_id": conv_id, "mode": "quiz"})

        body = resp.text
        self.assertIn("quiz", body)
        msgs = conversation_service.list_messages(conv_id)
        self.assertEqual(msgs[1]["message_type"], "quiz")
        self.assertEqual(msgs[1]["payload"]["quiz"][0]["question"], "q")

    def test_kb_not_ready_in_structured_mode(self):
        """结构化模式 KB 未构建 → error 事件（error_type=knowledge_base_not_ready），不写库"""
        client = _make_client()
        conv_id = client.post("/api/conversations", json={"subject_id": "数据结构"}).json()["data"]["id"]

        from services.kb_guard import KBError
        with mock.patch("services.note_service.require_kb_ready",
                        side_effect=KBError("knowledge_base_not_ready", "请先构建该课程的知识库")):
            resp = client.post("/api/chat/stream", json={
                "question": "二叉树", "subject_id": "数据结构",
                "conversation_id": conv_id, "mode": "smart_note"})

        self.assertIn("knowledge_base_not_ready", resp.text)
        msgs = conversation_service.list_messages(conv_id)
        # 用户消息已写，assistant 未写
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
