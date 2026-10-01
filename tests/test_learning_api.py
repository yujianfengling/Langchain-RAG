# -*- coding: utf-8 -*-
"""学习计划 API 集成测试（Task D / V2.5 集成修复）

使用 TestClient → FastAPI route → service → SQLite（隔离测试库）
Mock LLM Provider（不调用真实付费模型），真实 RAG 调用链验证。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from services import learning_service

# 隔离要求（2026-10-01）：测试自己的库也必须落在**本会话的数据目录**里
# （conftest 会把 EDU_DATA_DIR 指向隔离目录），不得直接写项目正式 data/。
from config.path_config import get_data_dir

TEST_DB = str(get_data_dir() / "test_learning_api.db")


def _make_client():
    from api.app import app
    return TestClient(app)


def _fake_call_llm(prompt, system_message=None, model=None, **kw):
    return '''{
  "title": "三天复习树和图",
  "goal": "三天复习完数据结构中的树和图",
  "days": [
    {"day": 1, "tasks": [
      {"knowledge_point": "树的基本概念", "task_type": "READ", "title": "阅读树的概念", "description": "d", "estimated_minutes": 30},
      {"knowledge_point": "二叉树", "task_type": "QUIZ", "title": "二叉树自测", "description": "d", "estimated_minutes": 20}
    ]},
    {"day": 2, "tasks": [
      {"knowledge_point": "DFS/BFS", "task_type": "PRACTICE", "title": "DFS练习", "description": "d", "estimated_minutes": 45}
    ]}
  ]
}'''


class LearningAPITest(unittest.TestCase):
    def setUp(self):
        learning_service.DB_PATH = TEST_DB
        learning_service._init_db()

    def tearDown(self):
        for p in (TEST_DB, TEST_DB + "-wal", TEST_DB + "-shm"):
            if os.path.exists(p):
                os.remove(p)

    def _patch_kb_ready(self):
        """让 KB 守卫放行（聚焦真实 SQLite/RAG/LLM-mock 链路）"""
        return mock.patch("services.kb_guard.check_kb_ready", return_value=None)

    def test_learning_plans_empty_returns_200(self):
        """无历史计划 → 200 + []，不报错"""
        client = _make_client()
        resp = client.get("/api/learning/plans")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_generate_plan_persists_to_sqlite(self):
        """数据结构生成计划（mock LLM）→ 200 + plan 存入 SQLite → 重新读取仍在"""
        client = _make_client()
        with mock.patch("services.learning_service.call_llm", side_effect=_fake_call_llm), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "树和图知识点", "context_used": True, "sources": []}), \
             self._patch_kb_ready():
            resp = client.post("/api/learning/plans/generate", json={
                "subject_id": "数据结构",
                "goal": "三天复习完数据结构中的树和图",
            })

        self.assertEqual(resp.status_code, 200, resp.text)
        plan = resp.json()["data"]
        self.assertEqual(plan["title"], "三天复习树和图")
        self.assertEqual(len(plan["tasks"]), 3)
        plan_id = plan["id"]

        # 重新读取（持久化验证）
        resp2 = client.get(f"/api/learning/plans/{plan_id}")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["data"]["tasks"][0]["status"], "TODO")

        # 更新任务状态 → 再读取保留
        task_id = plan["tasks"][0]["id"]
        resp3 = client.put(f"/api/learning/tasks/{task_id}/status", json={"status": "COMPLETED"})
        self.assertEqual(resp3.status_code, 200)
        resp4 = client.get(f"/api/learning/plans/{plan_id}")
        self.assertEqual(resp4.json()["data"]["tasks"][0]["status"], "COMPLETED")

    def test_learning_plans_filter_by_subject(self):
        """按学科过滤学习计划"""
        client = _make_client()
        with mock.patch("services.learning_service.call_llm", side_effect=_fake_call_llm), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "x", "context_used": True, "sources": []}), \
             self._patch_kb_ready():
            client.post("/api/learning/plans/generate", json={
                "subject_id": "数据结构", "goal": "复习树"})

        resp = client.get("/api/learning/plans", params={"subject_id": "数据结构"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["data"]), 1)
        resp2 = client.get("/api/learning/plans", params={"subject_id": "大学物理"})
        self.assertEqual(resp2.json()["data"], [])

    def test_learning_kb_not_ready_409(self):
        """知识库未构建（11）→ 409 + knowledge_base_not_ready，不调用 LLM"""
        client = _make_client()
        with mock.patch("services.kb_guard.check_kb_ready",
                        return_value="knowledge_base_not_ready"), \
             mock.patch("services.learning_service.call_llm") as mock_llm:
            resp = client.post("/api/learning/plans/generate", json={
                "subject_id": "11", "goal": "三天复习"})

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_type"], "knowledge_base_not_ready")
        mock_llm.assert_not_called()

    def test_learning_error_no_traceback_leak(self):
        """错误响应不泄露 traceback"""
        client = _make_client()
        with mock.patch("services.learning_service.call_llm",
                        side_effect=Exception("内部秘密错误信息")), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "x", "context_used": True, "sources": []}), \
             self._patch_kb_ready():
            resp = client.post("/api/learning/plans/generate", json={
                "subject_id": "数据结构", "goal": "复习"})

        # 500 或 503，且响应不含 traceback 细节
        self.assertIn(resp.status_code, (500, 503))
        self.assertNotIn("内部秘密错误信息", resp.text)
        self.assertNotIn("Traceback", resp.text)


if __name__ == "__main__":
    unittest.main()
