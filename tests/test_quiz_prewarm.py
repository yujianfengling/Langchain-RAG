"""
题库预热测试（建库后适度预生成）

锁定"适当"的边界：只预热前 N 个知识点、每点固定题量、同一学科只跑一次、
关闭开关后完全不跑、任何失败都不影响主流程。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.quiz_prewarm import (
    prewarm_subject_quiz,
    schedule_prewarm,
    reset_prewarm_state,
)


class TestQuizPrewarm(unittest.TestCase):

    def setUp(self):
        reset_prewarm_state()
        # 预热前会检查当日预算，而账本落在数据目录：
        # 必须隔离到临时目录，否则测试会读到真实用量、结果不可复现。
        self._tmp = tempfile.mkdtemp(prefix="prewarm_")
        self._env = mock.patch.dict(os.environ, {"EDU_DATA_DIR": self._tmp})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_only_limited_points_are_prewarmed(self):
        """只预热前 N 个知识点，且按配置的题量出题（避免烧光模型额度）"""
        points = [{"name": f"知识点{i}"} for i in range(20)]
        with mock.patch("services.learning_data.list_knowledge_points", return_value=points), \
             mock.patch("services.note_service.generate_quiz",
                        return_value={"quiz": [{"id": "q1"}]}) as gq, \
             mock.patch.dict(os.environ, {"WEB_SEARCH_PREGEN_POINTS": "3",
                                          "WEB_SEARCH_PREGEN_COUNT": "2"}):
            res = prewarm_subject_quiz("物理")

        self.assertFalse(res["skipped"])
        self.assertEqual(res["points"], 3)
        self.assertEqual(res["ok"], 3)
        self.assertEqual(gq.call_count, 3)
        self.assertEqual(gq.call_args.kwargs.get("count"), 2)

    def test_zero_points_disables_prewarm(self):
        with mock.patch.dict(os.environ, {"WEB_SEARCH_PREGEN_POINTS": "0"}):
            res = prewarm_subject_quiz("物理")
        self.assertTrue(res["skipped"])

    def test_no_knowledge_points_skips_quietly(self):
        with mock.patch("services.learning_data.list_knowledge_points", return_value=[]), \
             mock.patch.dict(os.environ, {"WEB_SEARCH_PREGEN_POINTS": "5"}):
            res = prewarm_subject_quiz("物理")
        self.assertTrue(res["skipped"])

    def test_single_point_failure_does_not_stop_others(self):
        """某个知识点出题失败不影响其它知识点"""
        def flaky(subject_id, topic, **kw):
            if topic == "知识点1":
                raise RuntimeError("model down")
            return {"quiz": [{"id": "q"}]}

        points = [{"name": "知识点1"}, {"name": "知识点2"}]
        with mock.patch("services.learning_data.list_knowledge_points", return_value=points), \
             mock.patch("services.note_service.generate_quiz", side_effect=flaky), \
             mock.patch.dict(os.environ, {"WEB_SEARCH_PREGEN_POINTS": "5"}):
            res = prewarm_subject_quiz("物理")

        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["ok"], 1)

    def test_schedule_runs_once_per_subject(self):
        """同一学科在同一进程内只预热一次（重复建库不反复烧额度）"""
        started = []

        class _FakeThread:
            def __init__(self, target=None, args=(), daemon=None, name=None):
                started.append(args)

            def start(self):
                pass

        with mock.patch.dict(os.environ, {"WEB_SEARCH_PREGEN_POINTS": "5"}), \
             mock.patch("services.quiz_prewarm.threading.Thread", _FakeThread):
            self.assertTrue(schedule_prewarm("物理"))
            self.assertFalse(schedule_prewarm("物理"))

        self.assertEqual(len(started), 1)


if __name__ == "__main__":
    unittest.main()
