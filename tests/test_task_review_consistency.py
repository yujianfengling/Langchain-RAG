"""完成任务必须同步关闭双写的复习任务（review_tasks + learning_tasks 两表一致）。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import learning_data, learning_service, review_service


class TestTaskReviewConsistency(unittest.TestCase):

    def setUp(self):
        # DB_PATH 是模块级常量：改环境变量无效，必须直接替换，否则读到的共享测试目录脏数据
        self._tmp = tempfile.mkdtemp(prefix="task_review_")
        self._p = mock.patch.multiple(
            learning_data, _DATA_DIR=self._tmp,
            DB_PATH=os.path.join(self._tmp, "learning_loop.db"))
        self._p.start()
        self.addCleanup(self._p.stop)
        learning_data._init_db()

    def test_complete_task_closes_linked_review_only(self):
        u, s = "u1", "sub1"
        kp = learning_data.ensure_knowledge_point(s, "二叉树")
        made = review_service.ensure_review_task(u, s, kp, "二叉树")
        plan_id = made.get("plan_task_id")
        self.assertTrue(plan_id, "复习双写应产生计划任务")
        kp2 = learning_data.ensure_knowledge_point(s, "排序")
        review_service.ensure_review_task(u, s, kp2, "排序")

        learning_service.complete_task(u, plan_id)

        pending = review_service.list_review_tasks(u, s, learning_data.REVIEW_PENDING)
        self.assertEqual([p["knowledge_point_id"] for p in pending], [kp2],
                         "只关来源对应的复习，不动其它知识点")
        done = review_service.list_review_tasks(u, s, learning_data.REVIEW_DONE)
        self.assertEqual([d["id"] for d in done], [made["task_id"]])

        again = learning_service.complete_task(u, plan_id)
        self.assertTrue(again["already_completed"])