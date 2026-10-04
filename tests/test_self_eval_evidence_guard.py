"""风险 B 回归：自评"答对"不得抹掉 AI 判错的证据（2026-10-03）

背景（代码事实）：
`quiz_service.self_evaluate` 此前**无条件**执行
`UPDATE quiz_attempts SET is_correct=?, grade_status='self_evaluated'`，
所以"AI 判错 → 学生自评答对"会把那条错证据**从数据库里改掉**；
`mastery_service.rebuild_point_from_attempts` 读库重算时只会看到"自评答对"
并 `continue` 跳过 —— 于是**任何一次后续重算**都会让 AI 的判错证据消失
（AGENTS.md 第 8 节声称"撤不掉那条错误证据"，与实际不符），
并留下"自评答对抹记录 + 另一次自评答错触发重算"的刷分路径。

修复：AI 已判过（ai_graded）且学生声称"答对"时，**保留 AI 的判定**，
只把学生声明写进 ai_feedback 留痕。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase  # noqa: E402

SUBJECT = "数据结构"
KP_NAME = "二叉树遍历"
ATTEMPT = "se-guard-1"


class SelfEvalEvidenceGuardTests(IsolatedDataTestCase):

    def _seed_ai_wrong_attempt(self):
        """直接写入一条"简答题 AI 判错"的作答（不依赖 LLM 判分）"""
        from services import quiz_service
        conn = quiz_service._get_db()
        try:
            conn.execute(
                """INSERT INTO quiz_attempts
                   (id, user_id, subject_id, question_id, knowledge_point_id, knowledge_point_name,
                    attempt_no, attempt_kind, user_answer, user_answer_text, correct_option_id,
                    is_correct, grade_status, ai_feedback, used_hint, revealed_answer, duration_sec,
                    source_task_id, conversation_id, client_token, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ATTEMPT, "se_user", SUBJECT, "q-se-1", "kp-se-1", KP_NAME,
                 1, "independent", "根右左", None, None,
                 0, "ai_graded", "AI判分：遍历顺序写反了", 0, 0, None, None, None,
                 "se-token-1", "2026-10-03 10:00:00"))
            conn.commit()
        finally:
            conn.close()

    def test_self_eval_correct_does_not_erase_ai_wrong_verdict(self):
        from services import mastery_service, quiz_service

        self._seed_ai_wrong_attempt()
        res = quiz_service.self_evaluate(ATTEMPT, "se_user", True)
        self.assertEqual(res["attempt_id"], ATTEMPT)

        # 1) 库里必须仍然是"AI 判错"，学生声明只留痕
        conn = quiz_service._get_db()
        try:
            row = conn.execute("SELECT is_correct, grade_status, ai_feedback "
                               "FROM quiz_attempts WHERE id=?", (ATTEMPT,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["is_correct"], 0,
                         "自评答对不得改写 AI 的判错结论（风险 B）")
        self.assertEqual(row["grade_status"], "ai_graded",
                         "AI 已判过的行不应被改成 self_evaluated")
        self.assertIn("学生自评：答对", row["ai_feedback"] or "",
                      "学生的声明应留痕（可审计）")

        # 2) 重算后，那条错证据仍然计入 → 掌握度仍是"弱"
        rebuilt = mastery_service.rebuild_point_from_attempts("se_user", SUBJECT, KP_NAME)
        level = rebuilt.get("mastery_level")
        self.assertIsNotNone(level, f"重算应产出掌握度: {rebuilt}")
        self.assertLess(level, 0.5,
                        f"AI 判错的证据在重算后仍应生效（掌握度应偏低），实际 {level}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
