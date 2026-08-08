"""
学习计划基础系统测试（V2.5 Task D）

覆盖：
- SQLite 持久化（plan + tasks CRUD）
- 任务状态流转（TODO→IN_PROGRESS→COMPLETED）
- AI 生成计划（mock LLM）+ 规范化
- 删除计划级联
- JSON 解析兼容
- 刷新后数据仍在（持久化）
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import learning_service
from services.learning_service import (
    LearningServiceError,
    list_plans,
    get_plan,
    delete_plan,
    update_task_status,
    generate_plan,
    _parse_plan_json,
)

# 隔离测试数据库
TEST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "test_learning.db")


def _make_llm_plan_output():
    return json.dumps({
        "title": "三天复习树和图",
        "goal": "三天复习完数据结构中的树和图",
        "days": [
            {"day": 1, "tasks": [
                {"knowledge_point": "树的基本概念", "task_type": "READ",
                 "title": "阅读树的基本概念", "description": "了解树的定义", "estimated_minutes": 30},
                {"knowledge_point": "二叉树", "task_type": "QUIZ",
                 "title": "二叉树自测", "description": "完成自测题", "estimated_minutes": 20},
            ]},
            {"day": 2, "tasks": [
                {"knowledge_point": "DFS/BFS", "task_type": "PRACTICE",
                 "title": "DFS/BFS练习", "description": "遍历练习", "estimated_minutes": 45},
            ]},
        ],
    }, ensure_ascii=False)


class LearningServiceTestBase(unittest.TestCase):
    def setUp(self):
        learning_service.DB_PATH = TEST_DB
        learning_service._init_db()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        for suffix in ("-wal", "-shm"):
            p = TEST_DB + suffix
            if os.path.exists(p):
                os.remove(p)


class TestPersistence(LearningServiceTestBase):
    def test_plan_task_crud(self):
        """计划+任务持久化：创建→查询→状态流转→删除"""
        with mock.patch("services.learning_service.call_llm",
                        return_value=_make_llm_plan_output()), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "树和图知识点", "context_used": True}):
            plan = generate_plan("数据结构", "三天复习完数据结构中的树和图")

        self.assertIsNotNone(plan)
        self.assertEqual(plan["title"], "三天复习树和图")
        self.assertEqual(len(plan["tasks"]), 3)
        # 任务类型规范化
        types = {t["task_type"] for t in plan["tasks"]}
        self.assertTrue({"READ", "QUIZ", "PRACTICE"}.issubset(types))
        # 天数推导 end_date
        self.assertIsNotNone(plan["end_date"])
        self.assertLessEqual(plan["start_date"], plan["end_date"])

        # 状态流转
        first_task = plan["tasks"][0]
        updated = update_task_status(first_task["id"], "IN_PROGRESS")
        self.assertEqual(updated["status"], "IN_PROGRESS")
        updated = update_task_status(first_task["id"], "COMPLETED")
        self.assertEqual(updated["status"], "COMPLETED")
        self.assertIsNotNone(updated["completed_at"])

        # 查询（刷新后数据仍在 = 持久化）
        reloaded = get_plan(plan["id"])
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["tasks"][0]["status"], "COMPLETED")

        # 删除级联
        ok = delete_plan(plan["id"])
        self.assertTrue(ok)
        self.assertIsNone(get_plan(plan["id"]))

    def test_invalid_status_rejected(self):
        with self.assertRaises(LearningServiceError):
            update_task_status("nonexistent", "INVALID_STATUS")


class TestPlanGeneration(LearningServiceTestBase):
    def test_generate_normalizes_tasks(self):
        with mock.patch("services.learning_service.call_llm",
                        return_value=_make_llm_plan_output()), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "x", "context_used": True}):
            plan = generate_plan("数据结构", "复习树和图")

        self.assertGreaterEqual(len(plan["tasks"]), 1)
        for t in plan["tasks"]:
            self.assertIn(t["task_type"], ("READ", "REVIEW", "QUIZ", "PRACTICE"))
            self.assertIn(t["status"], ("TODO", "IN_PROGRESS", "COMPLETED"))
            self.assertIsInstance(t["estimated_minutes"], int)
            self.assertGreaterEqual(t["estimated_minutes"], 5)

    def test_unparseable_raises(self):
        with mock.patch("services.learning_service.call_llm",
                        return_value="完全无法解析"), \
             mock.patch("services.learning_service.retrieve_and_build_context",
                        return_value={"context": "x", "context_used": True}):
            with self.assertRaises(LearningServiceError):
                generate_plan("数据结构", "复习")

    def test_empty_goal_rejected(self):
        with self.assertRaises(LearningServiceError):
            generate_plan("数据结构", "   ")


class TestPlanJsonParsing(unittest.TestCase):
    def test_markdown_wrapped(self):
        raw = '```json\n{"days":[]}\n```'
        self.assertEqual(_parse_plan_json(raw), {"days": []})

    def test_plain_json(self):
        raw = '{"title":"t","days":[{"day":1,"tasks":[]}]}'
        data = _parse_plan_json(raw)
        self.assertEqual(data["days"][0]["day"], 1)

    def test_garbage_returns_empty(self):
        self.assertEqual(_parse_plan_json("不是JSON"), {})


if __name__ == "__main__":
    unittest.main()
