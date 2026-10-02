"""
掌握度"按作答记录重算"的测试（2026-10-02）

背景：record_evidence 是增量累加 + 按 attempt_id 幂等，没有撤销接口。
一旦作答判定被事后推翻（学生用「我答错了」覆盖 AI 判分），
聚合值就和实际作答记录长期打架，且无法修正。

这里锁定三件事：
1. **重算结果必须与逐步累加完全一致**——否则只是又造了一个不一致源；
2. 判定被推翻后，重算能把聚合拉回与事实一致；
3. 重算不该抹掉与作答无关的数据（学习行为计数）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase
from services import learning_data, mastery_service

USER = "stu_rebuild"
SUBJECT = "数据结构"
KP = "二叉树"


class TestMasteryRebuild(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)

    def _insert_attempt(self, kind: str, correct: bool, at: str = None,
                        grade: str = "graded"):
        conn = learning_data._get_db()
        try:
            kp_id = learning_data.ensure_knowledge_point(SUBJECT, KP, source="test")
            conn.execute(
                """INSERT INTO quiz_attempts
                   (id, user_id, subject_id, question_id, knowledge_point_id,
                    knowledge_point_name, attempt_no, attempt_kind, is_correct,
                    grade_status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (learning_data._new_id("att"), USER, SUBJECT, "q_test", kp_id,
                 KP, 1, kind, 1 if correct else 0, grade,
                 at or learning_data._now()))
            conn.commit()
        finally:
            conn.close()

    def test_self_evaluated_wrong_is_counted(self):
        """单向从严：学生自评"答错"采信（承认不会，是有效证据）"""
        self._insert_attempt("independent", False, grade="self_evaluated")
        out = mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)
        self.assertEqual(out["evidence_count"], 1)
        self.assertEqual(out["independent_wrong"], 1)

    def test_self_evaluated_correct_is_not_counted(self):
        """单向从严：学生自评"答对"不采信（否则点一下就能刷掌握度）"""
        self._insert_attempt("independent", True, grade="self_evaluated")
        out = mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)
        self.assertEqual(out["evidence_count"], 0,
                         "自评答对被当成掌握证据了——这会给刷分留口子")
        self.assertEqual(out["independent_correct"], 0)
        self.assertIsNone(out["mastery_level"])

    def test_rebuild_matches_incremental_recording(self):
        """重算结果必须与逐步累加一致"""
        events = [("independent", True), ("independent", False),
                  ("review", True), ("after_reveal", True), ("independent", True)]
        for i, (kind, ok) in enumerate(events):
            self._insert_attempt(kind, ok)
            mastery_service.record_evidence(USER, SUBJECT, KP, ok, kind,
                                            source_attempt_id=f"att-{i}")

        before = mastery_service.get_mastery(USER, SUBJECT, KP)
        after = mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)

        self.assertTrue(after["rebuilt"])
        self.assertEqual(after["evidence_count"], before["evidence_count"])
        self.assertEqual(after["independent_correct"], before["independent_correct_count"])
        self.assertEqual(after["independent_wrong"], before["independent_wrong_count"])
        self.assertEqual(after["review_correct"], before["review_correct_count"])
        self.assertEqual(after["review_wrong"], before["review_wrong_count"])
        self.assertEqual(after["excluded_count"], before["excluded_count"])
        self.assertEqual(after["mastery_level"], before["mastery_level"])
        self.assertEqual(after["consolidation_status"], before["consolidation_status"])
        self.assertEqual(after["reason"], before["reason"],
                         "重算与增量给出的理由文案不一致（两处逻辑已经漂移）")

    def test_rebuild_corrects_superseded_verdict(self):
        """判定被事后推翻 → 重算把聚合拉回与事实一致"""
        self._insert_attempt("independent", True)
        mastery_service.record_evidence(USER, SUBJECT, KP, True, "independent",
                                        source_attempt_id="a1")
        self.assertEqual(mastery_service.get_mastery(USER, SUBJECT, KP)
                         ["independent_correct_count"], 1)

        # 模拟"学生把 AI 判的对改判为错"：作答记录改了，但增量聚合还停在"对"
        conn = learning_data._get_db()
        try:
            conn.execute("UPDATE quiz_attempts SET is_correct=0 WHERE user_id=?", (USER,))
            conn.commit()
        finally:
            conn.close()

        after = mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)
        self.assertEqual(after["independent_correct"], 0)
        self.assertEqual(after["independent_wrong"], 1)
        self.assertEqual(after["mastery_level"],
                         mastery_service.get_mastery(USER, SUBJECT, KP)["mastery_level"])

    def test_rebuild_keeps_behavior_count(self):
        """重算只碰证据，不抹掉学习行为计数"""
        self._insert_attempt("independent", True)
        mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)

        conn = mastery_service._get_db()
        try:
            conn.execute("UPDATE knowledge_mastery SET behavior_count=7 WHERE user_id=?", (USER,))
            conn.commit()
        finally:
            conn.close()

        mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)
        self.assertEqual(mastery_service.get_mastery(USER, SUBJECT, KP)["behavior_count"], 7)

    def test_rebuild_without_attempts_stays_pending(self):
        """没有任何作答时重算 → 待评估（不伪造一个分数）"""
        out = mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)
        self.assertTrue(out["rebuilt"])
        self.assertEqual(out["evidence_count"], 0)
        self.assertIsNone(out["mastery_level"])
        self.assertEqual(out["mastery_status"], "pending")

    def test_pending_attempts_are_not_counted(self):
        """没有判定结果的作答（is_correct 为空）不参与重算"""
        self._insert_attempt("independent", True)
        conn = learning_data._get_db()
        try:
            conn.execute("UPDATE quiz_attempts SET is_correct=NULL WHERE user_id=?", (USER,))
            conn.commit()
        finally:
            conn.close()

        out = mastery_service.rebuild_point_from_attempts(USER, SUBJECT, KP)
        self.assertEqual(out["evidence_count"], 0)
        self.assertEqual(out["attempts_replayed"], 0)

    def test_missing_params_returns_not_rebuilt(self):
        out = mastery_service.rebuild_point_from_attempts("", SUBJECT, KP)
        self.assertFalse(out["rebuilt"])


if __name__ == "__main__":
    unittest.main()
