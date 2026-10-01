"""
班级学情统计口径测试（2026-09-27）

修复的真实缺陷：`services/class_analytics._trend` 用 `is_correct is False` 判断答错，
而 SQLite 取出来的 `is_correct` 是**整数 0/1**，`0 is False` 为 False →
**「每日作答趋势」里的错误次数恒为 0、错误率恒为 0**（与同一份数据算出的知识点错误率自相矛盾）。
只有真实跑一遍界面/导出才会发现：自动测试此前没覆盖这条。

这里固定三件事：
1. 整数 0 必须被算作"答错"（这是本 bug 的核心）；
2. 未判分（None/空）既不计入错误，也不进趋势的分母；
3. 趋势与知识点统计的口径要一致：同一批作答，两处的"错误次数"合计必须相等。

全部在隔离数据目录运行，不调用模型、不写业务库。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import class_analytics as ca  # noqa: E402


def _attempt(user, qid, kp, correct, day="2026-09-27", grade="graded"):
    return {"user_id": user, "question_id": qid, "knowledge_point_id": kp,
            "knowledge_point_name": f"知识点{kp}", "is_correct": correct,
            "grade_status": grade, "attempt_kind": "independent",
            "created_at": f"{day}T10:00:00"}


class TestIsWrong(unittest.TestCase):
    """判定函数本身：整数 0 与字符串 '0' 都算答错"""

    def test_integer_zero_is_wrong(self):
        self.assertTrue(ca._is_wrong(0))

    def test_integer_one_is_not_wrong(self):
        self.assertFalse(ca._is_wrong(1))

    def test_false_and_string_zero_are_wrong(self):
        self.assertTrue(ca._is_wrong(False))
        self.assertTrue(ca._is_wrong("0"))
        self.assertTrue(ca._is_wrong("false"))

    def test_ungraded_is_neither(self):
        for v in (None, ""):
            self.assertFalse(ca._is_wrong(v), f"{v!r} 表示未判分，不应算答错")


class TestTrendCounts(unittest.TestCase):
    """趋势必须真的把错题算进去（本 bug 的回归测试）"""

    def test_trend_counts_integer_zero_as_wrong(self):
        rows = [
            _attempt("u1", "q1", "kp1", 1),
            _attempt("u1", "q2", "kp1", 0),
            _attempt("u2", "q1", "kp1", 0),
            _attempt("u2", "q2", "kp1", 1),
        ]
        trend = ca._trend(rows, {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual(len(trend), 1)
        day = trend[0]
        self.assertEqual(day["attempt_count"], 4)
        self.assertEqual(day["wrong_count"], 2,
                         "整数 0 必须算作答错（否则错误次数恒为 0）")
        self.assertEqual(day["wrong_rate"], 0.5)
        self.assertEqual(day["student_count"], 2)

    def test_trend_ignores_ungraded(self):
        rows = [
            _attempt("u1", "q1", "kp1", 0),
            _attempt("u1", "q2", "kp1", None, grade="pending"),   # 未判分
            _attempt("u1", "q3", "kp1", "", grade="pending"),     # 未判分
        ]
        trend = ca._trend(rows, {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual(trend[0]["attempt_count"], 1, "未判分的作答不应进分母")
        self.assertEqual(trend[0]["wrong_count"], 1)

    def test_trend_groups_by_day(self):
        rows = [
            _attempt("u1", "q1", "kp1", 0, day="2026-09-26"),
            _attempt("u1", "q2", "kp1", 1, day="2026-09-27"),
            _attempt("u2", "q1", "kp1", 0, day="2026-09-27"),
        ]
        trend = ca._trend(rows, {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual([t["date"] for t in trend], ["2026-09-26", "2026-09-27"])
        self.assertEqual(trend[0]["wrong_count"], 1)
        self.assertEqual(trend[1]["attempt_count"], 2)
        self.assertEqual(trend[1]["wrong_count"], 1)


class TestConsistentBasis(unittest.TestCase):
    """同一批作答：趋势的错误合计 == 知识点统计的原始错误合计"""

    def test_two_views_agree(self):
        rows = [
            _attempt("u1", "q1", "kp1", 0), _attempt("u1", "q2", "kp1", 1),
            _attempt("u2", "q1", "kp1", 0), _attempt("u2", "q2", "kp2", 0),
            _attempt("u3", "q1", "kp2", 1), _attempt("u3", "q2", "kp2", 0),
        ]
        trend = ca._trend(rows, {"start": "2026-09-01", "end": "2026-09-30"})
        trend_wrong = sum(t["wrong_count"] for t in trend)

        latest = ca._latest_attempts_per_question(rows)
        kps = ca._knowledge_point_stats(rows, latest)
        kp_raw_wrong = sum(k["raw_wrong_count"] for k in kps)

        self.assertEqual(trend_wrong, kp_raw_wrong,
                         "趋势与知识点统计必须基于同一个「答错」口径")
        # 6 次作答里答错 4 次（u1q1 / u2q1 / u2q2 / u3q2）
        self.assertEqual(trend_wrong, 4)


if __name__ == "__main__":
    unittest.main()
