"""
外部调用账本测试（成本可见）

锁定三件事：
1. 记账能聚合出"当天用了多少次"；
2. 预算能挡住后台预生成；
3. 记账本身**永远不能拖垮主流程**。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import usage_ledger
from services import quiz_prewarm


class TestUsageLedger(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="usage_")
        self._env = mock.patch.dict(os.environ, {"EDU_DATA_DIR": self._tmp})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_records_are_aggregated_by_kind(self):
        usage_ledger.record(usage_ledger.KIND_LLM, count=2)
        usage_ledger.record(usage_ledger.KIND_LLM)
        usage_ledger.record(usage_ledger.KIND_WEB_SEARCH)
        u = usage_ledger.get_usage()
        self.assertEqual(u["by_kind"][usage_ledger.KIND_LLM], 3)
        self.assertEqual(u["by_kind"][usage_ledger.KIND_WEB_SEARCH], 1)
        self.assertEqual(u["total"], 4)

    def test_budget_blocks_after_limit(self):
        with mock.patch.dict(os.environ, {"USAGE_DAILY_BUDGET_WEB_SEARCH": "2"}):
            usage_ledger.record(usage_ledger.KIND_WEB_SEARCH)
            self.assertFalse(usage_ledger.is_over_budget(usage_ledger.KIND_WEB_SEARCH))
            usage_ledger.record(usage_ledger.KIND_WEB_SEARCH)
            self.assertTrue(usage_ledger.is_over_budget(usage_ledger.KIND_WEB_SEARCH))

    def test_zero_budget_means_unlimited(self):
        with mock.patch.dict(os.environ, {"USAGE_DAILY_BUDGET_LLM": "0"}):
            usage_ledger.record(usage_ledger.KIND_LLM, count=9999)
            self.assertFalse(usage_ledger.is_over_budget(usage_ledger.KIND_LLM))

    def test_budget_status_exposes_used_and_limit(self):
        with mock.patch.dict(os.environ, {"USAGE_DAILY_BUDGET_LLM": "10"}):
            usage_ledger.record(usage_ledger.KIND_LLM, count=4)
            st = usage_ledger.budget_status()
        self.assertEqual(st["kinds"][usage_ledger.KIND_LLM]["used"], 4)
        self.assertEqual(st["kinds"][usage_ledger.KIND_LLM]["limit"], 10)
        self.assertFalse(st["kinds"][usage_ledger.KIND_LLM]["over"])

    def test_record_never_raises_when_db_is_broken(self):
        with mock.patch.object(usage_ledger, "_connect",
                               side_effect=RuntimeError("db down")):
            usage_ledger.record(usage_ledger.KIND_LLM)  # 不应抛异常

    def test_broken_db_reports_zero_instead_of_crashing(self):
        with mock.patch.object(usage_ledger, "_connect",
                               side_effect=RuntimeError("db down")):
            u = usage_ledger.get_usage()
        self.assertEqual(u["total"], 0)
        self.assertTrue(u.get("error"))

    def test_ledger_is_written_to_isolated_data_dir(self):
        usage_ledger.record(usage_ledger.KIND_LLM)
        self.assertTrue((os.path.join(self._tmp, "usage_ledger.db") and
                         os.path.exists(os.path.join(self._tmp, "usage_ledger.db"))))


class TestPrewarmRespectsBudget(unittest.TestCase):
    """预生成必须被预算挡住：后台预热不能烧光学生要用的额度"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="usage_")
        self._env = mock.patch.dict(os.environ, {"EDU_DATA_DIR": self._tmp})
        self._env.start()
        self.addCleanup(self._env.stop)
        quiz_prewarm.reset_prewarm_state()

    def test_prewarm_skips_when_llm_budget_exhausted(self):
        usage_ledger.record(usage_ledger.KIND_LLM, count=50)
        with mock.patch.dict(os.environ, {"WEB_SEARCH_PREGEN_POINTS": "5",
                                          "USAGE_DAILY_BUDGET_LLM": "10"}), \
             mock.patch("services.learning_data.list_knowledge_points",
                        return_value=[{"name": "kp"}]) as kp, \
             mock.patch("services.note_service.generate_quiz") as gq:
            res = quiz_prewarm.prewarm_subject_quiz("物理")

        self.assertTrue(res["skipped"])
        self.assertIn("预算", res["reason"])
        gq.assert_not_called()
        kp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
